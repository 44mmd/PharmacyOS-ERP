"""PharmacyOS POS — the server side of the shared counter screen (`/pos`).

One POS, two clients: the web browser and the Windows desktop app both open the same `/pos` page of
the pharmacy's ERP server, so there is a single POS implementation and a single database.

This module is an orchestration layer only. It never calculates a price, a tax, a discount, a batch, a
stock level or a refund itself, and it never raises the caller's rights:

* every document is built exactly the way the ERPNext POS screen builds it (a POS Sales Invoice — or a
  POS Invoice when POS Settings say so — with the counter's POS Profile) and is inserted and submitted
  **as the signed-in user** (no `ignore_permissions`), so ERPNext's controllers and every validated
  PharmacyOS hook run unchanged: stock locks and last-unit protection (`stock_guard`), FEFO and expired
  batch rejection (ERPNext's batch engine + `fefo`), return integrity and refund bounds (`returns`),
  website-reservation protection, cost confidentiality (`cost_privacy.scrub_response` filters every
  JSON answer of this module too);
* totals shown before payment come from ERPNext's own `set_missing_values` +
  `calculate_taxes_and_totals` on an unsaved copy of the invoice (`quote`);
* returns use ERPNext's return mapper, the same path as the Return button, and are validated by
  `pharmacy/returns.py`;
* the counter profile's `allow_rate_change` / `allow_discount_change` switches, which the ERPNext POS
  screen enforces only in the browser, are enforced here on the server.

What this module adds: idempotent checkout. Each cart attempt carries a client-generated request ID,
stored on the invoice in the DB-unique field `pharmacyos_pos_request_id`. Sending the same request again
(double click, network retry, two tabs) returns the first invoice instead of selling twice.
"""

import re
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import cint, flt

from pharmacyos_erp.branding import DESK_BASE, get_brand

REQUEST_FIELD = "pharmacyos_pos_request_id"
REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{15,63}$")
MAX_LINES = 200
RECEIPT_FORMATS = {"Sales Invoice": "PharmacyOS Receipt", "POS Invoice": "PharmacyOS POS Receipt"}


class POSRequestError(frappe.ValidationError):
	pass


# ------------------------------------------------------------------ helpers


def _require_user() -> None:
	if frappe.session.user == "Guest":
		raise frappe.AuthenticationError(_("Please sign in to use the POS."))


def invoice_doctype() -> str:
	"""The document the counter sells with — ERPNext's own POS setting."""
	if frappe.db.get_single_value("POS Settings", "invoice_type") == "POS Invoice":
		return "POS Invoice"
	return "Sales Invoice"


def _check_profile(pos_profile: str):
	from erpnext.selling.page.point_of_sale.point_of_sale import check_pos_profile_access

	check_pos_profile_access(pos_profile)
	profile = frappe.get_cached_doc("POS Profile", pos_profile)
	if profile.disabled:
		frappe.throw(_("This counter (POS Profile) is disabled."), frappe.PermissionError)
	users = {row.user for row in profile.applicable_for_users}
	if users and frappe.session.user not in users:
		# the counter's own user list (ERPNext's POS screen applies it only when picking the profile)
		frappe.throw(_("This counter (POS Profile) is not assigned to you."), frappe.PermissionError)
	return profile


def _user_profiles(user: str) -> list[dict]:
	"""Counters (POS Profiles) the user may open: readable, enabled, and assigned to them (or unassigned)."""
	names = frappe.get_list("POS Profile", filters={"disabled": 0}, pluck="name", order_by="name asc")
	if not names:
		return []
	assigned = {}
	for row in frappe.get_all(
		"POS Profile User", filters={"parent": ["in", names], "parenttype": "POS Profile"}, fields=["parent", "user"]
	):
		assigned.setdefault(row.parent, set()).add(row.user)
	out = []
	for name in names:
		if name in assigned and user not in assigned[name]:
			continue
		profile = frappe.get_cached_doc("POS Profile", name)
		out.append(
			{
				"name": name,
				"company": profile.company,
				"payments": [p.mode_of_payment for p in profile.payments],  # opening cash per method
				"payment_labels": {p.mode_of_payment: _(p.mode_of_payment) for p in profile.payments},
			}
		)
	return out


def _mode_types(modes: list[str]) -> dict:
	return dict(frappe.get_all("Mode of Payment", filters={"name": ["in", modes or [""]]}, fields=["name", "type"], as_list=True))


def _profile_info(profile) -> dict:
	modes = [p.mode_of_payment for p in profile.payments]
	types = _mode_types(modes)
	currency = profile.currency or frappe.get_cached_value("Company", profile.company, "default_currency")
	customer = profile.customer
	return {
		"name": profile.name,
		"company": profile.company,
		"warehouse": profile.warehouse,
		"currency": currency,
		"currency_symbol": _(frappe.db.get_value("Currency", currency, "symbol", cache=True) or currency),
		"price_list": profile.selling_price_list,
		"customer": customer,
		"customer_name": frappe.db.get_value("Customer", customer, "customer_name") if customer else None,
		"allow_rate_change": cint(profile.allow_rate_change),
		"allow_discount_change": cint(profile.allow_discount_change),
		"allow_partial_payment": cint(profile.allow_partial_payment),
		"hide_images": cint(profile.hide_images),
		"payments": [
			{
				"mode_of_payment": p.mode_of_payment,
				"label": _(p.mode_of_payment),
				"default": cint(p.default),
				"type": types.get(p.mode_of_payment),
			}
			for p in profile.payments
		],
		"print_format": profile.print_format or RECEIPT_FORMATS[invoice_doctype()],
	}


def _open_shift(user: str) -> dict | None:
	from erpnext.selling.page.point_of_sale.point_of_sale import check_opening_entry

	rows = check_opening_entry(user)
	return rows[0] if rows else None


def _clean_lines(items) -> list[dict]:
	items = frappe.parse_json(items) or []
	if not isinstance(items, list) or not items:
		frappe.throw(_("The cart is empty."), POSRequestError)
	if len(items) > MAX_LINES:
		frappe.throw(_("Too many lines in one sale."), POSRequestError)
	lines = []
	for raw in items:
		if not isinstance(raw, dict) or not isinstance(raw.get("item_code"), str):
			frappe.throw(_("Invalid cart line."), POSRequestError)
		qty = flt(raw.get("qty"))
		if qty <= 0:
			frappe.throw(_("Quantity must be greater than zero."), POSRequestError)
		line = {"item_code": raw["item_code"], "qty": qty}
		for key in ("uom", "batch_no"):
			if raw.get(key):
				line[key] = str(raw[key])
		if raw.get("discount_percentage") not in (None, "", 0):
			line["discount_percentage"] = flt(raw["discount_percentage"])
		if raw.get("rate") not in (None, ""):
			line["rate"] = flt(raw["rate"])
		lines.append(line)
	return lines


def _check_authority(profile, lines: list[dict], additional_discount_percentage: float) -> None:
	"""The counter's rate/discount switches — enforced on the server, not only in the screen."""
	if not cint(profile.allow_discount_change) and (
		flt(additional_discount_percentage) or any(flt(line.get("discount_percentage")) for line in lines)
	):
		frappe.throw(_("Discounts are not allowed on this counter."), frappe.PermissionError)
	if not cint(profile.allow_rate_change) and any("rate" in line for line in lines):
		frappe.throw(_("Changing the price is not allowed on this counter."), frappe.PermissionError)
	for line in lines:
		if not 0 <= flt(line.get("discount_percentage")) <= 100:
			frappe.throw(_("Discount must be between 0 and 100%."), POSRequestError)
		if "rate" in line and flt(line["rate"]) < 0:
			frappe.throw(_("Price cannot be negative."), POSRequestError)
	if not 0 <= flt(additional_discount_percentage) <= 100:
		frappe.throw(_("Discount must be between 0 and 100%."), POSRequestError)


def _build_invoice(pos_profile: str, items, customer: str | None, additional_discount_percentage: float):
	"""The unsaved counter invoice, as the ERPNext POS screen builds it, with ERPNext's defaults applied."""
	profile = _check_profile(pos_profile)
	doctype = invoice_doctype()
	if not frappe.has_permission(doctype, "create"):
		frappe.throw(_("You are not allowed to sell at the counter."), frappe.PermissionError)
	lines = _clean_lines(items)
	_check_authority(profile, lines, additional_discount_percentage)

	doc = frappe.new_doc(doctype)
	doc.update(
		{
			"is_pos": 1,
			"pos_profile": profile.name,
			"company": profile.company,
			"customer": customer or profile.customer,
		}
	)
	if doctype == "Sales Invoice":
		doc.is_created_using_pos = 1  # as the POS screen creates it (Sales Invoice mode)
	for line in lines:
		row = {"item_code": line["item_code"], "qty": line["qty"], "warehouse": profile.warehouse}
		if line.get("uom"):
			row["uom"] = line["uom"]
		if line.get("batch_no"):
			# a scanned batch barcode: the batch the cashier holds (FEFO guidance and expiry rules apply)
			row.update({"batch_no": line["batch_no"], "use_serial_batch_fields": 1})
		doc.append("items", row)

	doc.set_missing_values()  # ERPNext: POS profile, price list, item prices, taxes, accounts, payments
	for row, line in zip(doc.items, lines, strict=True):
		if "rate" in line:
			row.price_list_rate = row.price_list_rate or line["rate"]
			row.rate = line["rate"]
		if line.get("discount_percentage"):
			row.discount_percentage = line["discount_percentage"]
	if flt(additional_discount_percentage):
		doc.additional_discount_percentage = flt(additional_discount_percentage)
	doc.calculate_taxes_and_totals()
	# priced by ERPNext here, with the counter's switches checked above: the invoice guard only re-checks
	# the discount ceiling (an in-memory flag; a request cannot set it)
	from pharmacyos_erp.pharmacy.counter_pricing import PRICED_FLAG

	doc.flags[PRICED_FLAG] = True
	return doc, profile


def _summary(doc) -> dict:
	return {
		"doctype": doc.doctype,
		"name": doc.name if not doc.is_new() else None,
		"currency": doc.currency,
		"customer": doc.customer,
		"customer_name": doc.customer_name,
		"items": [
			{
				"idx": row.idx,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"uom": row.uom,
				"qty": flt(row.qty),
				"price_list_rate": flt(row.price_list_rate),
				"discount_percentage": flt(row.discount_percentage),
				"rate": flt(row.rate),
				"amount": flt(row.amount),
			}
			for row in doc.items
		],
		"total_qty": flt(doc.total_qty),
		"total": flt(doc.total),
		"discount_amount": flt(doc.discount_amount),
		"additional_discount_percentage": flt(doc.additional_discount_percentage),
		"taxes": [{"description": t.description, "tax_amount": flt(t.tax_amount)} for t in doc.taxes],
		"total_taxes_and_charges": flt(doc.total_taxes_and_charges),
		"grand_total": flt(doc.grand_total),
		"rounded_total": flt(doc.rounded_total),
		"rounding_adjustment": flt(doc.rounding_adjustment),
		"paid_amount": flt(doc.paid_amount),
		"change_amount": flt(doc.change_amount),
		"payments": [{"mode_of_payment": p.mode_of_payment, "amount": flt(p.amount)} for p in doc.payments],
		"is_return": cint(doc.get("is_return")),
		"return_against": doc.get("return_against"),
		"status": doc.get("status"),
		"docstatus": doc.docstatus,
		"posting_date": str(doc.posting_date) if doc.posting_date else None,
		"posting_time": str(doc.posting_time) if doc.posting_time else None,
	}


def _clean_request_id(request_id: str) -> str:
	if not isinstance(request_id, str) or not REQUEST_ID.match(request_id):
		frappe.throw(_("Invalid request ID."), POSRequestError)
	return request_id


def _existing(doctype: str, request_id: str, current: bool = False) -> dict | None:
	"""The invoice already created for this request, if it belongs to the caller.

	`current=True` is a locking read: it sees an invoice committed by a concurrent request after this
	transaction's REPEATABLE READ snapshot was taken (a plain read would not).
	"""
	existing = frappe.db.get_value(
		doctype, {REQUEST_FIELD: request_id}, ["name", "owner"], as_dict=True, for_update=current
	)
	if not existing:
		return None
	if existing.owner != frappe.session.user:
		frappe.throw(_("This request belongs to another user."), frappe.PermissionError)
	doc = frappe.get_doc(doctype, existing.name)
	doc.check_permission("read")
	return {**_summary(doc), "replayed": True}


def _post(doc, request_id: str) -> dict:
	"""Insert + submit as the user; a concurrent duplicate of the same request answers with the first."""
	doc.set(REQUEST_FIELD, request_id)
	frappe.db.savepoint("pharmacyos_pos_post")
	try:
		doc.insert()
		doc.submit()
	except Exception:
		# the same request may have been committed by a concurrent call (the unique request ID, or a
		# stock error raised only because that call already took the goods): answer with that invoice
		frappe.db.rollback(save_point="pharmacyos_pos_post")
		existing = _existing(doc.doctype, request_id, current=True)
		if existing:
			return existing
		raise
	return {**_summary(doc), "replayed": False}


def _payments(doc, payments, profile) -> None:
	"""Put the tendered amounts on ERPNext's payment rows (the counter's modes only)."""
	allowed = {p.mode_of_payment for p in profile.payments}
	given = {}
	for row in frappe.parse_json(payments) or []:
		mode = row.get("mode_of_payment") if isinstance(row, dict) else None
		if mode not in allowed:
			frappe.throw(_("Payment method {0} is not available on this counter.").format(mode), POSRequestError)
		amount = flt(row.get("amount"))
		if amount < 0:
			frappe.throw(_("Payment amount cannot be negative."), POSRequestError)
		given[mode] = given.get(mode, 0) + amount
	existing = {p.mode_of_payment for p in doc.payments}
	for mode in given:
		if mode not in existing:
			doc.append("payments", {"mode_of_payment": mode})
	for p in doc.payments:
		p.amount = given.get(p.mode_of_payment, 0)
	doc.calculate_taxes_and_totals()  # ERPNext: paid amount, change, write-off


# ------------------------------------------------------------------ endpoints


@frappe.whitelist()
def get_context() -> dict:
	"""Everything the POS screen needs to start: who is signed in, their counter and open shift."""
	_require_user()
	user = frappe.session.user
	doctype = invoice_doctype()
	shift = _open_shift(user)
	profile = None
	if shift:
		profile = _profile_info(_check_profile(shift.pos_profile))
	brand = get_brand()
	return {
		"user": user,
		"full_name": frappe.utils.get_fullname(user),
		"lang": frappe.local.lang,
		"invoice_doctype": doctype,
		"can_sell": cint(frappe.has_permission(doctype, "create")),
		"can_open_shift": cint(frappe.has_permission("POS Opening Entry", "create")),
		"can_close_shift": cint(frappe.has_permission("POS Closing Entry", "create")),
		# voiding needs the right to cancel sales; without it the counter asks a manager to approve
		"can_void": cint(frappe.has_permission(doctype, "cancel")),
		# the largest discount this user may give at the counter (100 for managers and the owner)
		"max_discount": _max_discount(),
		"version": _version(),
		"shift": shift,
		"profile": profile,
		"profiles": _user_profiles(user),
		"desk_base": DESK_BASE,
		"pharmacy": {
			k: brand["pharmacy"].get(k)
			for k in ("pharmacy_name", "pharmacy_name_ar", "pharmacy_logo")
			if brand.get("pharmacy")
		},
		"product_name": brand["product_name"],
	}


@frappe.whitelist()
def search_items(pos_profile: str, search_term: str = "", start: int = 0, page_length: int = 24) -> dict:
	"""ERPNext's POS item search/scan (barcode, batch, serial, name, Arabic and generic name), plus the
	Arabic name and what is sellable now (non-expired batches, earliest expiry first)."""
	from erpnext.selling.page.point_of_sale.point_of_sale import get_items, get_parent_item_group

	profile = _check_profile(pos_profile)
	page_length = min(max(cint(page_length), 1), 60)
	result = get_items(
		start=cint(start),
		page_length=page_length,
		price_list=profile.selling_price_list,
		item_group=get_parent_item_group(profile.name),
		pos_profile=profile.name,
		search_term=(search_term or "").strip(),
	)
	if isinstance(result, dict) and result.get("candidates"):
		rows = result["candidates"]
	else:
		rows = (result or {}).get("items", []) if isinstance(result, dict) else []
	# a barcode / batch / serial scan: ERPNext develop flags it; version-16 returns the single scanned
	# row, recognisable by its scan keys (a name search row never carries `serial_no`)
	scanned = bool(
		isinstance(result, dict)
		and (result.get("barcode_scan") or (len(rows) == 1 and "serial_no" in rows[0] and "barcode" in rows[0]))
	)
	codes = [r["item_code"] for r in rows if r.get("item_code")]
	meta = {
		r.name: r
		for r in frappe.get_all(
			"Item",
			filters={"name": ["in", codes or [""]]},
			fields=["name", "pharma_name_ar", "pharma_is_medicine", "has_batch_no", "pharma_dispensing"],
		)
	}
	items = []
	for r in rows:
		m = meta.get(r.get("item_code"))
		if not m:
			continue
		item = {
			"item_code": r["item_code"],
			"item_name": r.get("item_name"),
			"item_name_ar": m.pharma_name_ar,
			"image": None if cint(profile.hide_images) else r.get("item_image"),
			"uom": r.get("uom") or r.get("stock_uom"),
			"price_list_rate": flt(r.get("price_list_rate")),
			"currency": r.get("currency"),
			"actual_qty": flt(r.get("actual_qty")),
			"is_stock_item": cint(r.get("is_stock_item", 1)),
			"has_batch_no": cint(m.has_batch_no),
			"is_medicine": cint(m.pharma_is_medicine),
			"dispensing": m.pharma_dispensing,
			"barcode": r.get("barcode"),
			"batch_no": r.get("batch_no") if scanned else None,
		}
		if item["has_batch_no"]:
			item.update(_sellable(item["item_code"], profile.warehouse, item["batch_no"]))
		items.append(item)
	return {"items": items, "barcode_scan": scanned}


def _sellable(item_code: str, warehouse: str, scanned_batch: str | None) -> dict:
	"""Sellable stock of a batch item: non-expired batches only (ERPNext's FEFO query, read-only)."""
	from pharmacyos_erp.pharmacy.fefo import get_fefo_available

	batches = get_fefo_available(item_code, warehouse)
	ids = dict(
		frappe.get_all(
			"Batch",
			filters={"name": ["in", [b["batch_no"] for b in batches] + [scanned_batch or ""]]},
			fields=["name", "batch_id"],
			as_list=True,
		)
	)
	out = {
		"sellable_qty": sum(flt(b["qty"]) for b in batches),
		"next_batch": None,
		"scanned_batch": None,
	}
	if batches:
		b = batches[0]
		out["next_batch"] = {"batch_id": ids.get(b["batch_no"]), "expiry_date": str(b["expiry_date"] or "") or None}
	if scanned_batch:
		expiry = frappe.db.get_value("Batch", scanned_batch, "expiry_date")
		out["scanned_batch"] = {
			"batch_id": ids.get(scanned_batch) or frappe.db.get_value("Batch", scanned_batch, "batch_id"),
			"expiry_date": str(expiry) if expiry else None,
			"expired": bool(expiry and frappe.utils.getdate(expiry) < frappe.utils.getdate()),
		}
	return out


@frappe.whitelist(methods=["POST"])
def quote(pos_profile: str, items: list | str, customer: str | None = None, additional_discount_percentage: float = 0) -> dict:
	"""Totals of the cart as ERPNext calculates them (prices, pricing rules, discounts, taxes, rounding).
	Nothing is saved."""
	_require_user()
	doc, _profile = _build_invoice(pos_profile, items, customer, flt(additional_discount_percentage))
	from pharmacyos_erp.pharmacy.counter_pricing import check_discount_ceiling

	check_discount_ceiling(doc)  # the same limit checkout applies, shown before payment
	return _summary(doc)


@frappe.whitelist(methods=["POST"])
def checkout(
	pos_profile: str,
	items: list | str,
	payments: list | str,
	request_id: str,
	customer: str | None = None,
	additional_discount_percentage: float = 0,
) -> dict:
	"""Complete the sale: the counter invoice, submitted as the signed-in user. Idempotent by request ID."""
	_require_user()
	request_id = _clean_request_id(request_id)
	doctype = invoice_doctype()
	existing = _existing(doctype, request_id)
	if existing:
		return existing
	doc, profile = _build_invoice(pos_profile, items, customer, flt(additional_discount_percentage))
	_payments(doc, payments, profile)
	return _post(doc, request_id)


def _make_return(doctype: str, sale: str):
	if doctype == "POS Invoice":
		from erpnext.accounts.doctype.pos_invoice.pos_invoice import make_sales_return
	else:
		try:
			from erpnext.accounts.doctype.sales_invoice.mapper import make_sales_return
		except ImportError:
			from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
	return make_sales_return(sale)


def _load_sale(invoice: str):
	doctype = invoice_doctype()
	if not isinstance(invoice, str) or not invoice.strip():
		frappe.throw(_("Enter the invoice number."), POSRequestError)
	name = invoice.strip()
	if not frappe.db.exists(doctype, name) or not frappe.has_permission(doctype, "read", doc=name):
		frappe.throw(_("Invoice {0} was not found.").format(frappe.bold(name)), frappe.DoesNotExistError)
	sale = frappe.get_doc(doctype, name)
	if sale.docstatus != 1:
		frappe.throw(_("Only a completed (submitted) sale can be returned."), POSRequestError)
	if cint(sale.get("is_return")):
		frappe.throw(_("{0} is itself a return. Use the original sale.").format(frappe.bold(name)), POSRequestError)
	if cint(sale.get("is_consolidated")):
		frappe.throw(_("Consolidated invoices cannot be returned at the counter."), POSRequestError)
	return sale


@frappe.whitelist()
def get_return_candidate(invoice: str) -> dict:
	"""The lines of a sale that can still be returned (ERPNext's return mapper + the PharmacyOS ledger)."""
	_require_user()
	sale = _load_sale(invoice)
	from pharmacyos_erp.pharmacy.returns import returned_so_far

	ledger = returned_so_far(sale.doctype, sale.name, current=False)
	mapped = _make_return(sale.doctype, sale.name)
	left_by_item = {}
	for row in sale.items:
		left_by_item.setdefault(row.item_code, 0)
		left_by_item[row.item_code] += flt(row.stock_qty)
	for code in left_by_item:
		left_by_item[code] -= flt(ledger.qty.get(code))
	link = "sales_invoice_item" if sale.doctype == "Sales Invoice" else "pos_invoice_item"
	sold = {row.name: row for row in sale.items}
	lines = []
	for row in mapped.items:
		src = sold.get(row.get(link))
		if not src:
			continue
		cf = flt(src.conversion_factor) or 1
		mapper_left = -flt(row.qty)
		ledger_left = max(left_by_item.get(src.item_code, 0), 0) / cf
		returnable = max(min(mapper_left, ledger_left), 0)
		left_by_item[src.item_code] = max(left_by_item.get(src.item_code, 0) - returnable * cf, 0)
		lines.append(
			{
				"row": src.name,
				"item_code": src.item_code,
				"item_name": src.item_name,
				"item_name_ar": frappe.db.get_value("Item", src.item_code, "pharma_name_ar", cache=True),
				"uom": src.uom,
				"sold_qty": flt(src.qty),
				"returnable_qty": returnable,
				"rate": flt(src.rate),
			}
		)
	return {
		"invoice": sale.name,
		"doctype": sale.doctype,
		"customer_name": sale.customer_name,
		"posting_date": str(sale.posting_date),
		"grand_total": flt(sale.rounded_total or sale.grand_total),
		"currency": sale.currency,
		"payments": [p.mode_of_payment for p in sale.payments if flt(p.amount)],
		"lines": lines,
	}


def _build_return(invoice: str, lines, mode_of_payment: str | None):
	"""The counter return, unsaved: ERPNext's return mapper (the Return button), the chosen quantities,
	and ERPNext's recalculated refund paid out on one of the sale's payment methods."""
	doctype = invoice_doctype()
	sale = _load_sale(invoice)
	if not frappe.has_permission(doctype, "create"):
		frappe.throw(_("You are not allowed to make returns."), frappe.PermissionError)

	wanted = {}
	for row in frappe.parse_json(lines) or []:
		if not isinstance(row, dict) or not isinstance(row.get("row"), str):
			frappe.throw(_("Invalid return line."), POSRequestError)
		qty = flt(row.get("qty"))
		if qty < 0:
			frappe.throw(_("Quantity must be greater than zero."), POSRequestError)
		if qty:
			wanted[row["row"]] = wanted.get(row["row"], 0) + qty
	if not wanted:
		frappe.throw(_("Choose at least one item to return."), POSRequestError)

	ret = _make_return(sale.doctype, sale.name)
	link = "sales_invoice_item" if sale.doctype == "Sales Invoice" else "pos_invoice_item"
	if set(wanted) - {row.get(link) for row in ret.items}:
		frappe.throw(_("A line to return is not on this sale or was already returned in full."), POSRequestError)
	ret.set("items", [row for row in ret.items if row.get(link) in wanted])
	for row in ret.items:
		row.qty = -wanted[row.get(link)]
		row.stock_qty = row.qty * (flt(row.conversion_factor) or 1)
	ret.calculate_taxes_and_totals()  # ERPNext's refund for the chosen quantities

	# the refund is paid out on one of the sale's payment methods (cash by default), as at the counter
	modes = [p.mode_of_payment for p in ret.payments]
	mode = mode_of_payment if mode_of_payment in modes else None
	if not mode:
		types = _mode_types(modes)
		cash = [m for m in modes if types.get(m) == "Cash"]
		mode = (cash or modes or [None])[0]
	refund = flt(ret.rounded_total or ret.grand_total)
	for p in ret.payments:
		p.amount = refund if p.mode_of_payment == mode else 0
	ret.calculate_taxes_and_totals()
	return ret


@frappe.whitelist(methods=["POST"])
def preview_return(invoice: str, lines: list | str, mode_of_payment: str | None = None) -> dict:
	"""The refund ERPNext calculates for the chosen quantities. Nothing is saved."""
	_require_user()
	return _summary(_build_return(invoice, lines, mode_of_payment))


@frappe.whitelist(methods=["POST"])
def submit_return(invoice: str, lines: list | str, request_id: str, mode_of_payment: str | None = None) -> dict:
	"""Submit the counter return as the signed-in user, validated by `pharmacy/returns.py` (quantities,
	batches, refund bound, credit-note authority, concurrency). Idempotent by request ID."""
	_require_user()
	request_id = _clean_request_id(request_id)
	existing = _existing(invoice_doctype(), request_id)
	if existing:
		return existing
	return _post(_build_return(invoice, lines, mode_of_payment), request_id)


@frappe.whitelist()
def recent_sales(limit: int = 20, search: str | None = None) -> list[dict]:
	"""The signed-in user's latest counter sales, returns and voided sales (for reprint, return and void).
	`search` matches the invoice number or the customer's name."""
	_require_user()
	doctype = invoice_doctype()
	filters = {"owner": frappe.session.user, "docstatus": ["in", [1, 2]], "is_pos": 1}
	or_filters = None
	text = (search or "").strip()
	if text:
		like = f"%{text}%"
		or_filters = {"name": ["like", like], "customer_name": ["like", like]}
	rows = frappe.get_list(
		doctype,
		filters=filters,
		or_filters=or_filters,
		fields=["name", "customer_name", "grand_total", "rounded_total", "currency", "posting_date", "posting_time", "is_return", "return_against", "status", "docstatus"],
		order_by="creation desc",
		limit_page_length=min(max(cint(limit), 1), 100),
	)
	today = frappe.utils.nowdate()
	for row in rows:
		row["voided"] = row.docstatus == 2
		row["can_void"] = row.docstatus == 1 and not row.is_return and str(row.posting_date) == today
	# a sale already counted in a closed shift is corrected by a return, not a void (void_sale refuses it)
	closed = _closed_shift_sales(doctype, [r.name for r in rows if r["can_void"]])
	for row in rows:
		if row.name in closed:
			row["can_void"] = False
	return rows


def _closed_shift_sales(doctype: str, names: list[str]) -> set[str]:
	if not names:
		return set()
	child, field = ("POS Invoice Reference", "pos_invoice") if doctype == "POS Invoice" else ("Sales Invoice Reference", "sales_invoice")
	refs = frappe.get_all(child, filters={field: ["in", names], "parenttype": "POS Closing Entry"}, fields=["parent", field])
	submitted = set(frappe.get_all("POS Closing Entry", filters={"name": ["in", list({r.parent for r in refs}) or [""]], "docstatus": 1}, pluck="name"))
	return {r[field] for r in refs if r.parent in submitted}


def _max_discount() -> float:
	from pharmacyos_erp.pharmacy.counter_pricing import is_counter_only, max_counter_discount

	return max_counter_discount() if is_counter_only() else 100.0


def _version() -> str:
	from pharmacyos_erp import __version__

	return __version__


# ------------------------------------------------------------------ closing the shift at the counter


def _own_open_shift():
	"""The signed-in user's open shift as a document they may read (never someone else's)."""
	shift = _open_shift(frappe.session.user)
	if not shift:
		frappe.throw(_("You have no open shift."), POSRequestError)
	opening = frappe.get_doc("POS Opening Entry", shift.name)
	opening.check_permission("read")
	return opening


def _closing_for(opening):
	"""ERPNext's own closing entry for the shift, unsaved: its sales, payments and expected cash."""
	from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import make_closing_entry_from_opening

	closing = make_closing_entry_from_opening(opening)
	# ERPNext's server builder leaves the opening float out (its desk form adds it): the drawer holds the
	# opening cash plus the takings, so that is what the cashier is expected to count
	rows = {row.mode_of_payment: row for row in closing.payment_reconciliation}
	for detail in opening.balance_details:
		row = rows.get(detail.mode_of_payment)
		if row is None:
			row = closing.append("payment_reconciliation", {"mode_of_payment": detail.mode_of_payment, "opening_amount": 0, "expected_amount": 0})
			rows[detail.mode_of_payment] = row
		row.opening_amount = flt(detail.opening_amount)
		row.expected_amount = flt(row.expected_amount) + flt(detail.opening_amount)
	return closing


def _closing_summary(closing) -> dict:
	return {
		"name": closing.name if not closing.is_new() else None,
		"shift": closing.pos_opening_entry,
		"pos_profile": closing.pos_profile,
		"sales": len(closing.get("sales_invoices") or closing.get("pos_transactions") or []),
		"grand_total": flt(closing.grand_total),
		"net_total": flt(closing.net_total),
		"payments": [
			{
				"mode_of_payment": row.mode_of_payment,
				"label": _(row.mode_of_payment),
				"opening_amount": flt(row.opening_amount),
				"expected_amount": flt(row.expected_amount),
				"closing_amount": flt(row.closing_amount),
				"difference": flt(row.difference),
			}
			for row in closing.payment_reconciliation
		],
	}


@frappe.whitelist()
def shift_summary() -> dict:
	"""What the cashier's open shift took, per payment method (opening + takings = expected). Saves nothing."""
	_require_user()
	return _closing_summary(_closing_for(_own_open_shift()))


@frappe.whitelist(methods=["POST"])
def close_shift(counted: list | str) -> dict:
	"""Close the cashier's own shift with the amounts counted in the drawer, as ERPNext's POS Closing Entry
	(submitted as the signed-in user, so ERPNext's rules and permissions apply). Every payment method of
	the shift must be counted; the difference to the expected amount is recorded, never corrected."""
	_require_user()
	if not frappe.has_permission("POS Closing Entry", "create"):
		frappe.throw(_("You are not allowed to close a shift."), frappe.PermissionError)
	counted = frappe.parse_json(counted) or []
	if not isinstance(counted, list):
		frappe.throw(_("Invalid counted amounts."), POSRequestError)
	amounts = {}
	for row in counted:
		if not isinstance(row, dict) or not isinstance(row.get("mode_of_payment"), str):
			frappe.throw(_("Invalid counted amounts."), POSRequestError)
		value = row.get("closing_amount")
		if value in (None, "") or flt(value) < 0:
			frappe.throw(_("Enter the amount counted for {0}.").format(_(row["mode_of_payment"])), POSRequestError)
		amounts[row["mode_of_payment"]] = flt(value)

	opening = _own_open_shift()
	# one closing per shift, even for a double click racing itself: lock the shift, then re-check it
	status = frappe.db.get_value("POS Opening Entry", opening.name, "status", for_update=True)
	if status != "Open":
		frappe.throw(_("This shift is already closed."), POSRequestError)
	closing = _closing_for(opening)
	missing = [row.mode_of_payment for row in closing.payment_reconciliation if row.mode_of_payment not in amounts]
	if missing:
		frappe.throw(_("Enter the amount counted for {0}.").format(", ".join(_(m) for m in missing)), POSRequestError)
	for row in closing.payment_reconciliation:
		row.closing_amount = amounts[row.mode_of_payment]
		row.difference = flt(row.closing_amount) - flt(row.expected_amount)
	closing.insert()
	closing.submit()
	return _closing_summary(closing)


# ------------------------------------------------------------------ voiding a sale at the counter
#
# A void is ERPNext's cancel of the counter invoice: stock goes back to the batches it came from, the
# ledger is reversed, and the invoice stays on record as Cancelled — nothing is deleted. Only a user with
# the right to cancel sales (manager, owner, accountant) can void; a cashier asks one to approve at the
# counter (the approver signs with their own password, and the cancel runs as the approver). Every void
# writes a PharmacyOS Void Log: the sale, amount, reason, who asked, who approved and when.

APPROVAL_FAILURES_KEY = "pharmacyos_pos_void_approval_failures"
MAX_APPROVAL_FAILURES = 5  # per requesting user, per 10 minutes


def _closing_reference(doctype: str, name: str) -> str | None:
	"""The submitted POS Closing Entry that already counted this sale, if any (its shift is closed)."""
	child, field = ("POS Invoice Reference", "pos_invoice") if doctype == "POS Invoice" else ("Sales Invoice Reference", "sales_invoice")
	rows = frappe.get_all(child, filters={field: name, "parenttype": "POS Closing Entry"}, pluck="parent")
	for parent in rows:
		if frappe.db.get_value("POS Closing Entry", parent, "docstatus") == 1:
			return parent
	return None


@contextmanager
def _acting_as(user: str):
	"""Run as another user inside this request, then give the signed-in user back their own session.
	(`frappe.set_user` also replaces the session id and data: used alone it would sign the cashier out.)"""
	session = frappe.local.session
	saved = {"user": session.user, "sid": session.sid, "data": session.data}
	form = frappe.local.form_dict
	try:
		frappe.set_user(user)
		yield
	finally:
		frappe.set_user(saved["user"])
		session.sid = saved["sid"]
		session.data = saved["data"]
		frappe.local.form_dict = form


def _approver(user: str | None, password: str | None) -> str:
	"""A second person's approval: their own sign-in password, checked like a login (with a failure limit)."""
	from frappe.utils.password import check_password

	requester = frappe.session.user
	key = f"{APPROVAL_FAILURES_KEY}:{requester}"
	if cint(frappe.cache.get_value(key)) >= MAX_APPROVAL_FAILURES:
		frappe.throw(_("Too many failed approvals. Wait a few minutes and try again."), frappe.PermissionError)
	if not user or not password:
		frappe.throw(_("A manager must approve this void with their email and password."), frappe.PermissionError)
	user = str(user).strip()
	if user == requester:
		frappe.throw(_("Another person must approve the void."), frappe.PermissionError)
	try:
		check_password(user, password)
		if not frappe.db.get_value("User", {"name": user, "enabled": 1, "user_type": "System User"}):
			raise frappe.AuthenticationError
	except frappe.AuthenticationError:
		frappe.cache.set_value(key, cint(frappe.cache.get_value(key)) + 1, expires_in_sec=600)
		frappe.throw(_("The approval was refused: wrong email or password."), frappe.PermissionError)
	return user


@frappe.whitelist(methods=["POST"])
def void_sale(invoice: str, reason: str, approver: str | None = None, approver_password: str | None = None) -> dict:
	"""Void (cancel) a counter sale of today whose shift is still open. Idempotent: voiding a sale that is
	already voided returns its void record."""
	_require_user()
	doctype = invoice_doctype()
	reason = (reason or "").strip()
	if len(reason) < 3:
		frappe.throw(_("Enter the reason for the void."), POSRequestError)
	if not frappe.db.exists(doctype, invoice):
		frappe.throw(_("Sale {0} not found.").format(invoice), POSRequestError)

	existing = frappe.db.get_value("PharmacyOS Void Log", {"invoice": invoice}, "name")
	if existing and frappe.db.get_value(doctype, invoice, "docstatus") == 2:
		return _void_summary(frappe.get_doc("PharmacyOS Void Log", existing))

	# one void at a time per sale (a double click, two counters)
	frappe.db.get_value(doctype, invoice, "name", for_update=True)
	doc = frappe.get_doc(doctype, invoice)
	requester = frappe.session.user
	if not frappe.has_permission(doctype, "read", doc):
		frappe.throw(_("Sale {0} not found.").format(invoice), POSRequestError)
	if doc.docstatus != 1 or not cint(doc.is_pos):
		frappe.throw(_("Only a completed counter sale can be voided."), POSRequestError)
	if cint(doc.is_return):
		frappe.throw(_("A return cannot be voided."), POSRequestError)
	if frappe.db.exists(doctype, {"return_against": invoice, "docstatus": 1}):
		frappe.throw(_("Items of this sale were returned; it can no longer be voided."), POSRequestError)
	if str(doc.posting_date) != frappe.utils.nowdate():
		frappe.throw(_("Only sales of today can be voided. Use a return for older sales."), POSRequestError)
	closed_in = _closing_reference(doctype, invoice)
	if closed_in:
		frappe.throw(_("This sale belongs to a closed shift ({0}). Use a return instead.").format(closed_in), POSRequestError)

	if frappe.has_permission(doctype, "cancel", doc):
		approved_by = requester
	else:
		if doc.owner != requester:
			frappe.throw(_("You can only ask to void your own sales."), frappe.PermissionError)
		approved_by = _approver(approver, approver_password)
		if not frappe.has_permission(doctype, "cancel", doc, user=approved_by):
			frappe.throw(_("{0} is not allowed to void sales.").format(approved_by), frappe.PermissionError)

	# the cancel runs as the approving user, so ERPNext's own rules and every PharmacyOS hook apply
	with _acting_as(approved_by):
		doc = frappe.get_doc(doctype, invoice)
		doc.cancel()

	log = frappe.get_doc(
		{
			"doctype": "PharmacyOS Void Log",
			"invoice_doctype": doctype,
			"invoice": invoice,
			"amount": flt(doc.rounded_total or doc.grand_total),
			"currency": doc.currency,
			"voided_on": frappe.utils.now_datetime(),
			"requested_by": requester,
			"approved_by": approved_by,
			"pos_profile": doc.pos_profile,
			"sale_owner": doc.owner,
			"reason": reason[:1000],
		}
	)
	log.insert(ignore_permissions=True)  # written by this function only; read-only for everyone
	doc.add_comment(
		"Info",
		_("Voided at the counter by {0}, approved by {1}: {2}").format(requester, approved_by, reason[:500]),
	)
	return _void_summary(log)


def _void_summary(log) -> dict:
	return {
		"name": log.name,
		"invoice": log.invoice,
		"amount": flt(log.amount),
		"reason": log.reason,
		"requested_by": log.requested_by,
		"approved_by": log.approved_by,
		"voided_on": str(log.voided_on),
		"status": "voided",
	}
