"""Counter staff sell at the counter: their own shift, today's date and the pharmacy's prices — enforced on
the invoice itself, whatever made it (the POS screen, the desk form or the document API).

A cashier holds the right to create and submit Sales Invoices (the POS needs it), so without these rules a
sale could be sent through the document API with any date, outside any shift, or at any price. For users
whose only selling authority is the counter (Cashier, Pharmacist — no manager/owner role), every sale and
counter return (`validate_counter_sale`, Sales Invoice / POS Invoice validate):

* is a counter (POS) invoice of the user's OWN open shift on that counter — so every sale and refund is in
  a shift the user counts at close (ERPNext only checks that *someone's* shift is open on the profile);
* is dated today — no back-dated sale (ERPNext checks batch expiry against the posting date, so a back-dated
  sale could dispense a batch that has since expired) and no post-dated one;

and every sale (not a return: returns are bound to the original sale by `returns.py`) is checked against a
priced copy made by ERPNext itself (`set_missing_values` + `calculate_taxes_and_totals`, the POS screen's
path):

* the list price of every line must be the price list's price (no invented list price);
* the rate may only be lower than the list price through a discount, and only on a counter that allows
  discounts (POS Profile `allow_discount_change`); a direct rate change only where `allow_rate_change`;
* whole-sale discounts likewise need `allow_discount_change`;
* no discount above PharmacyOS Settings → Maximum Counter Discount (%) (default 10 %), per line and for
  the sale as a whole; above that a pharmacy manager or the owner completes the sale.

Pricing rules (automatic offers) are part of ERPNext's priced copy, so they always pass, and the ceiling
counts only what the counter takes off on top of them (a 20 % promotion is not a 20 % counter discount).
Managers, the owner, the accountant and System Managers are not limited. A sale built by `pos/api.py` was
priced the same way a moment earlier and is marked so (in-memory flags a request cannot set, with the
rates ERPNext charged before the counter's discount); only the discount ceiling is re-checked for it.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

PRICE_AUTHORITY_ROLES = ("Pharmacy Owner", "Pharmacy Manager", "Sales Manager", "Accounts Manager", "System Manager")
COUNTER_ROLES = ("Cashier", "Pharmacist")
DEFAULT_MAX_DISCOUNT = 10.0
PRICED_FLAG = "pharmacyos_priced"
BASE_RATES = "pharmacyos_base_rates"  # flag: per line, the rate ERPNext charges before any counter discount


class CounterPriceError(frappe.PermissionError):
	pass


class CounterShiftError(frappe.PermissionError):
	pass


def validate_counter_sale(doc, method=None):
	"""doc_event (validate) for Sales Invoice and POS Invoice."""
	if cint(doc.get("is_consolidated")) or frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return
	if not is_counter_only():
		return
	user = frappe.session.user
	if not cint(doc.get("is_pos")) or not doc.get("pos_profile"):
		frappe.throw(_("Counter staff sell through the point of sale, in their own shift."), CounterShiftError)
	if getdate(doc.posting_date) != getdate(nowdate()):
		frappe.throw(_("Counter sales and returns are recorded with today's date."), CounterShiftError)
	if not frappe.db.exists("POS Opening Entry", {"user": user, "pos_profile": doc.pos_profile, "status": "Open", "docstatus": 1}):
		frappe.throw(
			_("Open your shift on {0} before selling or refunding there.").format(frappe.bold(doc.pos_profile)), CounterShiftError
		)
	guard_counter_pricing(doc)


def is_counter_only(user: str | None = None) -> bool:
	roles = set(frappe.get_roles(user))
	return bool(roles & set(COUNTER_ROLES)) and not roles & set(PRICE_AUTHORITY_ROLES)


def max_counter_discount() -> float:
	# the stored value itself: a setting never saved reads as 0 through get_single_value, which would
	# forbid every counter discount on a pharmacy updated from an older version
	raw = _stored_ceiling()
	return DEFAULT_MAX_DISCOUNT if raw in (None, "") else min(max(flt(raw), 0.0), 100.0)


def _stored_ceiling():
	row = frappe.db.sql(
		"select value from `tabSingles` where doctype='PharmacyOS Settings' and field='max_counter_discount'"
	)
	return row[0][0] if row else None


def ensure_default_setting() -> None:
	"""after_migrate: store the default ceiling on pharmacies set up before the setting existed."""
	if _stored_ceiling() in (None, ""):
		frappe.db.set_single_value("PharmacyOS Settings", "max_counter_discount", DEFAULT_MAX_DISCOUNT)


def guard_counter_pricing(doc, method=None):
	if cint(doc.get("is_return")) or frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return
	if not is_counter_only():
		return
	tolerance = 0.5 * 10 ** -(cint(frappe.get_precision(doc.doctype, "grand_total")) or 0)
	profile = frappe.get_cached_doc("POS Profile", doc.pos_profile) if cint(doc.get("is_pos")) and doc.get("pos_profile") else None
	allow_discount = bool(profile and cint(profile.allow_discount_change))
	allow_rate = bool(profile and cint(profile.allow_rate_change))
	# the counter's own price list: a customer's (or customer group's) default list is not a way around it
	if profile and profile.selling_price_list and doc.selling_price_list != profile.selling_price_list:
		frappe.throw(
			_("Counter sales use the counter's price list ({0}).").format(frappe.bold(profile.selling_price_list)),
			CounterPriceError,
		)

	reference = None
	if not doc.flags.get(PRICED_FLAG):
		reference = _priced_copy(doc)
		for row, ref in zip(doc.items, reference.items, strict=True):
			label = _("Row {0}: {1}").format(row.idx, frappe.bold(row.item_code))
			if abs(flt(row.price_list_rate) - flt(ref.price_list_rate)) > tolerance:
				frappe.throw(
					_("{0}: the price must be the pharmacy's price ({1}).").format(label, frappe.format(ref.price_list_rate, {"fieldtype": "Currency", "options": doc.currency})),
					CounterPriceError,
				)
			# what ERPNext's own pricing (list price, pricing rules) charges for this line
			if flt(row.rate) < flt(ref.rate) - tolerance:
				reduced_by_discount = flt(row.discount_percentage) > 0 or flt(row.discount_amount) > 0
				if reduced_by_discount and not allow_discount:
					frappe.throw(_("{0}: discounts are not allowed on this counter.").format(label), CounterPriceError)
				if not reduced_by_discount and not allow_rate:
					frappe.throw(_("{0}: changing the price is not allowed on this counter.").format(label), CounterPriceError)
		if (flt(doc.additional_discount_percentage) > 0 or flt(doc.discount_amount) > 0) and not allow_discount:
			frappe.throw(_("Discounts are not allowed on this counter."), CounterPriceError)

	check_discount_ceiling(doc, reference)


def check_discount_ceiling(doc, reference=None) -> None:
	"""The discount ceiling for counter staff, per line and for the whole sale, as a share of the list
	prices. Only the counter's own discount counts: the rate ERPNext charges with its pricing rules
	(`reference`, or the rates `pos/api.py` recorded) is the starting point. Also used by the POS quote, so
	the cashier sees it before payment."""
	if not is_counter_only():
		return
	tolerance = 0.5 * 10 ** -(cint(frappe.get_precision(doc.doctype, "grand_total")) or 0)
	limit = max_counter_discount()
	base_rates = doc.flags.get(BASE_RATES) or ([flt(r.rate) for r in reference.items] if reference else None)
	listed = offered = 0.0
	for i, row in enumerate(doc.items):
		plr = flt(row.price_list_rate)
		base = min(flt(base_rates[i]), plr) if base_rates and i < len(base_rates) and plr > 0 else plr
		listed += plr * flt(row.qty)
		offered += base * flt(row.qty)
		if plr > 0 and (base - flt(row.rate)) / plr * 100 > limit + 1e-6:
			frappe.throw(
				_("Row {0}: a discount above {1}% needs a pharmacy manager.").format(row.idx, frappe.format(limit, {"fieldtype": "Float"})),
				CounterPriceError,
			)
	charged = flt(doc.net_total)
	if listed > 0 and (offered - charged) / listed * 100 > limit + 1e-6 and offered - charged > tolerance:
		frappe.throw(
			_("A discount above {0}% of the sale needs a pharmacy manager.").format(frappe.format(limit, {"fieldtype": "Float"})),
			CounterPriceError,
		)


def _priced_copy(doc):
	"""The same sale priced by ERPNext from scratch (no rates, discounts or pricing rules carried over)."""
	copy = frappe.copy_doc(doc)
	copy.flags.ignore_permissions = True
	copy.additional_discount_percentage = 0
	copy.discount_amount = 0
	for row in copy.items:
		for field in ("rate", "price_list_rate", "discount_percentage", "discount_amount", "margin_rate_or_amount", "pricing_rules", "base_rate", "base_price_list_rate"):
			row.set(field, None)
	copy.set_missing_values()
	copy.calculate_taxes_and_totals()
	return copy


CUSTOMER_TERMS = ("customer_group", "default_price_list", "payment_terms", "default_currency")


def guard_customer_terms(doc, method=None):
	"""doc_event (validate) for Customer. Counter staff add customers at the counter (name, phone) and
	correct their contact details, but not the terms that decide prices and credit: customer group (group
	promotions and price lists), default price list, payment terms, currency, credit limits. A new customer
	gets the pharmacy's default group. (A loyalty programme is assigned by ERPNext from the rules the
	managers define, so it is not checked here.)"""
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch or not is_counter_only():
		return
	refused = _("Only a pharmacy manager or the owner changes a customer's group, price list, loyalty or credit terms.")
	if doc.is_new():
		defaults = {
			frappe.db.get_single_value("Selling Settings", "customer_group"),
			frappe.db.get_default("customer_group"),
			frappe.defaults.get_user_default("customer_group"),
		} - {None, ""}
		if doc.customer_group and doc.customer_group not in defaults:
			frappe.throw(refused, CounterPriceError)
		if any(doc.get(f) for f in ("default_price_list", "payment_terms")) or doc.get("credit_limits"):
			frappe.throw(refused, CounterPriceError)
		return
	before = doc.get_doc_before_save()
	if not before:
		return
	changed = [f for f in CUSTOMER_TERMS if (before.get(f) or None) != (doc.get(f) or None)]
	limits = lambda d: sorted((r.company, flt(r.credit_limit), cint(r.bypass_credit_limit_check)) for r in d.get("credit_limits") or [])
	if changed or limits(before) != limits(doc):
		frappe.throw(refused, CounterPriceError)
