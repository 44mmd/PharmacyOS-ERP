"""Pharmacy Report — what the owner and managers need to understand a day, a week or a month.

Sales, returns, discounts, voids, payment methods, top medicines, sales per cashier and shift variances
for a period (and branch), from the submitted ERPNext documents the user may read. Purchases, stock value
and gross profit are cost information: they are included only for roles that may see costs
(`cost_privacy.can_see_costs`), otherwise sent as None. Counter staff (Cashier, Pharmacist) cannot open
the report at all.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from pharmacyos_erp.permissions import require_pharmacy_role
from pharmacyos_erp.pharmacy.cost_privacy import can_see_costs, page_costs
from pharmacyos_erp.pharmacy.dashboard import _scope, gross_profit, sales_summary, top_sellers

REPORT_ROLES = ("Pharmacy Owner", "Pharmacy Manager", "Pharmacy Accountant", "System Manager")
MAX_DAYS = 366


def _check_access() -> None:
	require_pharmacy_role()
	if not set(frappe.get_roles()) & set(REPORT_ROLES):
		frappe.throw(_("The pharmacy report is for the owner, managers and the accountant."), frappe.PermissionError)


def _payment_breakdown(names: list[str]) -> list[dict]:
	"""Money received per payment method (returns negative); the change given back is taken off cash."""
	if not names:
		return []
	rows = frappe.get_all(
		"Sales Invoice Payment",
		filters={"parent": ["in", names], "parenttype": "Sales Invoice"},
		fields=["parent", "mode_of_payment", "type", "base_amount"],
	)
	change = dict(
		frappe.get_all("Sales Invoice", filters={"name": ["in", names]}, fields=["name", "base_change_amount"], as_list=True)
	)
	totals = defaultdict(float)
	counts = defaultdict(set)
	change_taken = set()
	for r in rows:
		amount = flt(r.base_amount)
		if r.type == "Cash" and r.parent not in change_taken and flt(change.get(r.parent)):
			amount -= flt(change[r.parent])
			change_taken.add(r.parent)
		if amount:
			totals[r.mode_of_payment] += amount
			counts[r.mode_of_payment].add(r.parent)
	return sorted(
		({"mode_of_payment": m, "label": _(m), "amount": flt(v, 2), "count": len(counts[m])} for m, v in totals.items()),
		key=lambda x: -abs(x["amount"]),
	)


def _discounts(names: list[str]) -> dict:
	if not names:
		return {"total": 0.0, "invoices": 0}
	sale_level = frappe.get_all(
		"Sales Invoice", filters={"name": ["in", names], "is_return": 0}, fields=["name", "base_discount_amount"]
	)
	lines = frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": ["in", names], "parenttype": "Sales Invoice", "discount_amount": [">", 0]},
		fields=["parent", "qty", "discount_amount"],
	)
	returns = set(frappe.get_all("Sales Invoice", filters={"name": ["in", names], "is_return": 1}, pluck="name"))
	total, with_discount = 0.0, set()
	for s in sale_level:
		if flt(s.base_discount_amount):
			total += flt(s.base_discount_amount)
			with_discount.add(s.name)
	for line in lines:
		if line.parent in returns:
			continue
		total += flt(line.qty) * flt(line.discount_amount)
		with_discount.add(line.parent)
	return {"total": flt(total, 2), "invoices": len(with_discount)}


def _by_cashier(names: list[str]) -> list[dict]:
	if not names:
		return []
	rows = frappe.get_all(
		"Sales Invoice", filters={"name": ["in", names]}, fields=["owner", "is_return", "base_grand_total"]
	)
	out = {}
	for r in rows:
		row = out.setdefault(r.owner, {"user": r.owner, "name": frappe.utils.get_fullname(r.owner), "sales": 0, "returns": 0, "total": 0.0})
		row["returns" if r.is_return else "sales"] += 1
		row["total"] += flt(r.base_grand_total)
	return sorted(({**v, "total": flt(v["total"], 2)} for v in out.values()), key=lambda x: -x["total"])


def _counters(warehouses) -> list[str] | None:
	"""The counters (POS Profiles) of the branch scope; None = every counter."""
	if warehouses is None:
		return None
	return frappe.get_all("POS Profile", filters={"warehouse": ["in", warehouses or [""]]}, pluck="name")


def _voids(from_date: str, to_date: str, counters=None) -> dict:
	filters = {"voided_on": ["between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"]]}
	if counters is not None:
		filters["pos_profile"] = ["in", counters or [""]]
	rows = frappe.get_list(
		"PharmacyOS Void Log",
		filters=filters,
		fields=["name", "invoice", "amount", "reason", "requested_by", "approved_by", "voided_on"],
		order_by="voided_on desc",
	)
	return {"count": len(rows), "amount": flt(sum(flt(r.amount) for r in rows), 2), "rows": rows[:50]}


def _shifts(from_date: str, to_date: str, counters=None) -> list[dict]:
	if not frappe.has_permission("POS Opening Entry", "read"):
		return None
	filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}
	if counters is not None:
		filters["pos_profile"] = ["in", counters or [""]]
	openings = frappe.get_list(
		"POS Opening Entry",
		filters=filters,
		fields=["name", "user", "pos_profile", "period_start_date", "status"],
		order_by="period_start_date desc",
		limit_page_length=200,
	)
	if not openings:
		return []
	closings = {
		c.pos_opening_entry: c
		for c in frappe.get_all(
			"POS Closing Entry",
			filters={"pos_opening_entry": ["in", [o.name for o in openings]], "docstatus": 1},
			fields=["name", "pos_opening_entry", "period_end_date", "grand_total"],
		)
	}
	recon = defaultdict(lambda: {"expected": 0.0, "counted": 0.0, "difference": 0.0})
	if closings:
		for r in frappe.get_all(
			"POS Closing Entry Detail",
			filters={"parent": ["in", [c.name for c in closings.values()]]},
			fields=["parent", "expected_amount", "closing_amount", "difference"],
		):
			agg = recon[r.parent]
			agg["expected"] += flt(r.expected_amount)
			agg["counted"] += flt(r.closing_amount)
			agg["difference"] += flt(r.difference)
	out = []
	for o in openings:
		closing = closings.get(o.name)
		agg = recon[closing.name] if closing else None
		out.append(
			{
				"shift": o.name,
				"user": o.user,
				"name": frappe.utils.get_fullname(o.user),
				"counter": o.pos_profile,
				"opened": o.period_start_date,
				"closed": closing.period_end_date if closing else None,
				"status": o.status,
				"sales_total": flt(closing.grand_total, 2) if closing else None,
				"expected": flt(agg["expected"], 2) if agg else None,
				"counted": flt(agg["counted"], 2) if agg else None,
				"difference": flt(agg["difference"], 2) if agg else None,
			}
		)
	return out


def _purchases(from_date: str, to_date: str, warehouses) -> dict | None:
	if not can_see_costs() or not frappe.has_permission("Purchase Receipt", "read"):
		return None
	filters = [["docstatus", "=", 1], ["is_return", "=", 0], ["posting_date", "between", [from_date, to_date]]]
	if warehouses is not None:
		filters.append(["Purchase Receipt Item", "warehouse", "in", warehouses or [""]])
	names = frappe.get_list("Purchase Receipt", filters=filters, pluck="name", distinct=True, limit_page_length=0)
	total = flt(frappe.get_all("Purchase Receipt", filters={"name": ["in", names or [""]]}, fields=[{"SUM": "base_grand_total", "as": "t"}])[0].t)
	returns = frappe.get_list(
		"Purchase Receipt",
		filters=[["docstatus", "=", 1], ["is_return", "=", 1], ["posting_date", "between", [from_date, to_date]]],
		pluck="name",
		limit_page_length=0,
	)
	return {"receipts": len(names), "total": flt(total, 2), "supplier_returns": len(returns)}


def _stock_value(warehouses) -> float | None:
	if not can_see_costs() or not frappe.has_permission("Bin", "read"):
		return None
	filters = {"actual_qty": ["!=", 0]}
	if warehouses is not None:
		filters["warehouse"] = ["in", warehouses or [""]]
	return flt(frappe.get_all("Bin", filters=filters, fields=[{"SUM": "stock_value", "as": "v"}])[0].v, 2)


@frappe.whitelist()
def get_sales_report(from_date: str | None = None, to_date: str | None = None, branch: str | None = None) -> dict:
	_check_access()
	to_date = str(getdate(to_date or nowdate()))
	from_date = str(getdate(from_date or to_date))
	if getdate(from_date) > getdate(to_date):
		frappe.throw(_("The start date is after the end date."))
	if (getdate(to_date) - getdate(from_date)).days > MAX_DAYS:
		frappe.throw(_("Choose a period of at most one year."))
	warehouses = _scope(branch)
	summary = sales_summary(from_date, to_date, warehouses)
	if summary is None:
		frappe.throw(_("You do not have permission to view sales."), frappe.PermissionError)
	names = summary["si_names"]
	return_rows = frappe.get_all(
		"Sales Invoice", filters={"name": ["in", names or [""]], "is_return": 1}, fields=[{"SUM": "base_grand_total", "as": "t"}]
	)
	returns_amount = abs(flt(return_rows[0].t)) if return_rows else 0.0
	sales_amount = flt(summary["total"]) + returns_amount
	transactions = summary["transactions"]
	return page_costs(
		{
			"from_date": from_date,
			"to_date": to_date,
			"sales": {
				"gross": flt(sales_amount, 2),
				"returns": flt(returns_amount, 2),
				"net": flt(summary["total"], 2),
				"transactions": transactions,
				"returns_count": summary["returns"],
				"average": flt(sales_amount / transactions, 2) if transactions else 0.0,
			},
			"discounts": _discounts(names),
			"voids": _voids(from_date, to_date, _counters(warehouses)),
			"payments": _payment_breakdown(names),
			"top_items": top_sellers(names, summary["pos_names"], limit=10),
			"cashiers": _by_cashier(names),
			"shifts": _shifts(from_date, to_date, _counters(warehouses)),
			"purchases": _purchases(from_date, to_date, warehouses),
			"stock_value": _stock_value(warehouses),
			"gross_profit": gross_profit(names, summary["pos_names"]) if can_see_costs() else None,
			"costs_visible": cint(can_see_costs()),
		}
	)


@frappe.whitelist()
def report_periods() -> dict:
	"""Preset periods for the report's buttons (server dates, the pharmacy's time zone)."""
	today = getdate(nowdate())
	return {
		"today": [str(today), str(today)],
		"yesterday": [str(add_days(today, -1)), str(add_days(today, -1))],
		"week": [str(add_days(today, -6)), str(today)],
		"month": [str(today.replace(day=1)), str(today)],
	}
