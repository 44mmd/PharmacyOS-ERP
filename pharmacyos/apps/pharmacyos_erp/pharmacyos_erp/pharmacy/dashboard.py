"""Pharmacy command center — real ERPNext data only.

Permission model:
* Sales/purchase figures are computed over documents returned by `frappe.get_list`, so role and
  User Permissions apply. Child-table quantities are only read for those permitted documents.
* A user restricted by User Permissions to certain warehouses/branches is always scoped to them,
  even when "All branches" is selected.
* Each section returns `None` when the user lacks read access to its source DocType, and the UI
  shows it as unavailable instead of a misleading zero.

Definitions (shown in the UI as notes):
* Sales today: submitted Sales Invoices (not POS-consolidation invoices) + submitted POS Invoices,
  returns included as negative amounts; grand total in company currency.
* Gross profit: only for Sales Invoices that update stock (their COGS is in the stock ledger).
  Marked partial when other invoice types exist today.
"""

import frappe
from frappe.utils import add_days, cint, flt, nowdate

from pharmacyos_erp.permissions import require_pharmacy_role
from pharmacyos_erp.pharmacy.branches import get_permitted_warehouses, resolve_warehouses
from pharmacyos_erp.pharmacy.cost_privacy import can_see_costs, page_costs

SALES_VOUCHERS = ("Sales Invoice", "Delivery Note", "POS Invoice")


def _can(doctype: str) -> bool:
	return bool(frappe.has_permission(doctype, "read"))


def _is_restricted(doctype: str) -> bool:
	return bool(frappe.db.exists("User Permission", {"user": frappe.session.user, "allow": doctype}))


def _scope(branch: str | None) -> list[str] | None:
	"""Warehouses in view, or None for 'no warehouse restriction'."""
	scope = resolve_warehouses(branch)
	if scope is None and (_is_restricted("Warehouse") or _is_restricted("Branch")):
		scope = get_permitted_warehouses()
	return scope


def _invoice_names(doctype: str, filters: list, warehouses: list[str] | None, child: str) -> list[str]:
	if not _can(doctype):
		return None
	filters = list(filters)
	if warehouses is not None:
		filters.append([child, "warehouse", "in", warehouses or [""]])
	return frappe.get_list(doctype, filters=filters, pluck="name", distinct=True, limit_page_length=0)


def _sum(doctype: str, names: list[str], fields: list[str]) -> dict:
	if not names:
		return {f["as"]: 0 for f in fields}
	row = frappe.get_all(doctype, filters={"name": ["in", names]}, fields=fields)[0]
	return {k: flt(v) for k, v in row.items()}


def sales_summary(from_date: str, to_date: str, warehouses):
	si = _invoice_names(
		"Sales Invoice",
		[
			["docstatus", "=", 1],
			["is_consolidated", "=", 0],
			["posting_date", "between", [from_date, to_date]],
		],
		warehouses,
		"Sales Invoice Item",
	)
	pos = _invoice_names(
		"POS Invoice",
		[["docstatus", "=", 1], ["posting_date", "between", [from_date, to_date]]],
		warehouses,
		"POS Invoice Item",
	)
	if si is None and pos is None:
		return None
	si, pos = si or [], pos or []
	fields = [
		{"SUM": "base_grand_total", "as": "total"},
		{"SUM": "base_net_total", "as": "net"},
		{"COUNT": "name", "as": "count"},
	]
	a, b = _sum("Sales Invoice", si, fields), _sum("POS Invoice", pos, fields)
	returns = 0
	for dt, names in (("Sales Invoice", si), ("POS Invoice", pos)):
		if names:
			returns += cint(frappe.db.count(dt, {"name": ["in", names], "is_return": 1}))
	mine = 0.0
	for dt, names in (("Sales Invoice", si), ("POS Invoice", pos)):
		if names:
			mine += flt(
				frappe.get_all(
					dt,
					filters={"name": ["in", names], "owner": frappe.session.user},
					fields=[{"SUM": "base_grand_total", "as": "t"}],
				)[0].t
			)
	return {
		"total": flt(a["total"] + b["total"], 2),
		"net": flt(a["net"] + b["net"], 2),
		"transactions": cint(a["count"] + b["count"]) - returns,
		"returns": returns,
		"mine": flt(mine, 2),
		"si_names": si,
		"pos_names": pos,
	}


def gross_profit(si_names: list[str], pos_names: list[str]):
	if not si_names and not pos_names:
		return {"value": 0.0, "margin": None, "partial": False}
	stock_si = frappe.get_all(
		"Sales Invoice", filters={"name": ["in", si_names or [""]], "update_stock": 1}, pluck="name"
	)
	partial = len(stock_si) < len(si_names) or bool(pos_names)
	if not stock_si:
		return {"value": None, "margin": None, "partial": True}
	revenue = flt(
		frappe.get_all(
			"Sales Invoice", filters={"name": ["in", stock_si]}, fields=[{"SUM": "base_net_total", "as": "v"}]
		)[0].v
	)
	cogs = -flt(
		frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_type": "Sales Invoice", "voucher_no": ["in", stock_si], "is_cancelled": 0},
			fields=[{"SUM": "stock_value_difference", "as": "v"}],
		)[0].v
	)
	value = revenue - cogs
	return {
		"value": flt(value, 2),
		"margin": flt(100 * value / revenue, 1) if revenue else None,
		"partial": partial,
	}


def top_sellers(si_names, pos_names, limit=6):
	rows = {}
	for child, names in (("Sales Invoice Item", si_names), ("POS Invoice Item", pos_names)):
		if not names:
			continue
		for r in frappe.get_all(
			child,
			filters={"parent": ["in", names]},
			fields=[
				"item_code",
				"item_name",
				{"SUM": "stock_qty", "as": "qty"},
				{"SUM": "base_net_amount", "as": "amount"},
			],
			group_by="item_code",
		):
			agg = rows.setdefault(
				r.item_code, {"item_code": r.item_code, "item_name": r.item_name, "qty": 0.0, "amount": 0.0}
			)
			agg["qty"] += flt(r.qty)
			agg["amount"] += flt(r.amount)
	out = sorted((r for r in rows.values() if r["qty"] > 0), key=lambda r: r["amount"], reverse=True)[:limit]
	ar = dict(
		frappe.get_all(
			"Item",
			filters={"name": ["in", [r["item_code"] for r in out] or [""]]},
			fields=["name", "pharma_name_ar"],
			as_list=True,
		)
	)
	for r in out:
		r["name_ar"] = ar.get(r["item_code"])
	return out


def slow_movers(warehouses, days=60, limit=6):
	if not _can("Stock Ledger Entry") or not _can("Bin"):
		return None
	bin_filters = {"actual_qty": [">", 0]}
	if warehouses is not None:
		bin_filters["warehouse"] = ["in", warehouses or [""]]
	fields = ["item_code", {"SUM": "actual_qty", "as": "qty"}]
	# ranked by stock value for cost-reading roles, by quantity otherwise (no value to rank by)
	fields.append({"SUM": "stock_value" if can_see_costs() else "actual_qty", "as": "value"})
	stocked = frappe.get_all("Bin", filters=bin_filters, fields=fields, group_by="item_code")
	if not stocked:
		return []
	sle_filters = {
		"posting_date": [">=", add_days(nowdate(), -days)],
		"actual_qty": ["<", 0],
		"voucher_type": ["in", SALES_VOUCHERS],
		"is_cancelled": 0,
		"item_code": ["in", [b.item_code for b in stocked]],
	}
	if warehouses is not None:
		sle_filters["warehouse"] = ["in", warehouses or [""]]
	sold = set(frappe.get_all("Stock Ledger Entry", filters=sle_filters, pluck="item_code", distinct=True))
	slow = sorted((b for b in stocked if b.item_code not in sold), key=lambda b: flt(b.value), reverse=True)[
		:limit
	]
	names = {
		i.name: i
		for i in frappe.get_all(
			"Item",
			filters={"name": ["in", [s.item_code for s in slow] or [""]]},
			fields=["name", "item_name", "pharma_name_ar"],
		)
	}
	return [
		{
			"item_code": s.item_code,
			"item_name": names.get(s.item_code, {}).get("item_name"),
			"name_ar": names.get(s.item_code, {}).get("pharma_name_ar"),
			"qty": flt(s.qty, 3),
			"value": flt(s.value, 2),
		}
		for s in slow
	]


def recent_movements(warehouses, limit=8):
	if not _can("Stock Ledger Entry"):
		return None
	filters = {"is_cancelled": 0}
	if warehouses is not None:
		filters["warehouse"] = ["in", warehouses or [""]]
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters=filters,
		fields=[
			"item_code",
			"warehouse",
			"actual_qty",
			"voucher_type",
			"voucher_no",
			"posting_date",
			"posting_time",
		],
		order_by="posting_datetime desc, creation desc",
		limit_page_length=limit,
	)
	names = dict(
		frappe.get_all(
			"Item",
			filters={"name": ["in", [r.item_code for r in rows] or [""]]},
			fields=["name", "item_name"],
			as_list=True,
		)
	)
	for r in rows:
		r["item_name"] = names.get(r.item_code)
	return rows


def recent_sales(si_names, limit=8):
	if si_names is None:
		return None
	if not si_names:
		return []
	return frappe.get_all(
		"Sales Invoice",
		filters={"name": ["in", si_names]},
		fields=[
			"name",
			"customer_name",
			"base_grand_total",
			"posting_date",
			"posting_time",
			"is_return",
			"status",
			"owner",
		],
		order_by="posting_date desc, posting_time desc",
		limit_page_length=limit,
	)


PURCHASING_ROLES = {
	"Pharmacy Owner",
	"Pharmacy Manager",
	"Inventory Manager",
	"Purchasing Officer",
	"Pharmacy Accountant",
	"System Manager",
}


def purchasing_summary(warehouses):
	"""Purchasing/finance panels are shown to purchasing-related roles only (presentation choice;
	access itself is governed by ERPNext permissions)."""
	out = {"pending_orders": None, "draft_receipts": None, "supplier_obligations": None}
	if frappe.session.user != "Administrator" and not (set(frappe.get_roles()) & PURCHASING_ROLES):
		return out
	if _can("Purchase Order"):
		filters = [["docstatus", "=", 1], ["status", "in", ["To Receive and Bill", "To Receive"]]]
		if warehouses is not None:
			filters.append(["Purchase Order Item", "warehouse", "in", warehouses or [""]])
		names = frappe.get_list(
			"Purchase Order", filters=filters, pluck="name", distinct=True, limit_page_length=0
		)
		value = _sum("Purchase Order", names, [{"SUM": "base_grand_total", "as": "v"}])["v"] if names else 0
		out["pending_orders"] = {"count": len(names), "value": flt(value, 2)}
	else:
		out["pending_orders"] = None
	if _can("Purchase Receipt"):
		out["draft_receipts"] = len(
			frappe.get_list("Purchase Receipt", filters={"docstatus": 0}, pluck="name", limit_page_length=0)
		)
	else:
		out["draft_receipts"] = None
	if _can("Purchase Invoice"):
		names = frappe.get_list(
			"Purchase Invoice",
			filters={"docstatus": 1, "outstanding_amount": [">", 0]},
			pluck="name",
			limit_page_length=0,
		)
		owed = (
			_sum("Purchase Invoice", names, [{"SUM": "outstanding_amount", "as": "v"}])["v"] if names else 0
		)
		out["supplier_obligations"] = {"count": len(names), "value": flt(owed, 2)}
	else:
		out["supplier_obligations"] = None
	return out


def system_attention() -> list[dict]:
	from pharmacyos_erp.pharmacy.system import attention_items

	try:
		return attention_items()
	except Exception:  # a status problem must never break the dashboard
		frappe.log_error(title="PharmacyOS dashboard: system status")
		return []


@frappe.whitelist()
def get_dashboard(branch: str | None = None) -> dict:
	require_pharmacy_role()
	from pharmacyos_erp.pharmacy.expiry import get_batches
	from pharmacyos_erp.pharmacy.inventory import get_inventory_health

	today = nowdate()
	warehouses = _scope(branch)
	sales = sales_summary(today, today, warehouses)
	week = sales_summary(add_days(today, -6), today, warehouses)
	month = sales_summary(add_days(today, -29), today, warehouses)

	stock = None
	if _can("Bin") and _can("Item"):
		health = get_inventory_health(branch=branch, page_length=1)
		batches = get_batches(branch=branch, page_length=1)
		negative = frappe.db.count(
			"Bin",
			{
				"actual_qty": ["<", 0],
				**({"warehouse": ["in", warehouses or [""]]} if warehouses is not None else {}),
			},
		)
		stock = {
			"inventory_value": health["inventory_value"],
			"counts": health["counts"],
			"expired_batches": batches["counts"]["expired"],
			"expired_value": batches["value"]["expired"],
			"expiring_30": batches["counts"]["30"],
			"expiring_30_value": batches["value"]["30"],
			"expiring_90": batches["counts"]["90"],
			"negative_bins": negative,
		}

	return page_costs(
		{
			"date": today,
			"currency": frappe.get_cached_value(
				"Company", frappe.defaults.get_user_default("Company"), "default_currency"
			)
			if frappe.defaults.get_user_default("Company")
			else None,
			"sales": None if sales is None else {k: v for k, v in sales.items() if not k.endswith("_names")},
			"week": None if week is None else {k: v for k, v in week.items() if not k.endswith("_names")},
			"gross_profit": None
			if sales is None or not _can("Stock Ledger Entry")
			else gross_profit(sales["si_names"], sales["pos_names"]),
			"top_sellers": None if month is None else top_sellers(month["si_names"], month["pos_names"]),
			"slow_movers": slow_movers(warehouses),
			"recent_sales": recent_sales(None if sales is None else week["si_names"]),
			"recent_movements": recent_movements(warehouses),
			"stock": stock,
			"purchasing": purchasing_summary(warehouses),
			"system": system_attention(),
			"user": {"full_name": frappe.utils.get_fullname()},
		}
	)
