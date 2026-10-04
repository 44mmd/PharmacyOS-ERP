"""Inventory health and reorder suggestions (read-only, server-side aggregation).

Quantities come from ERPNext's Bin (per item + warehouse) and Item Reorder; batch expiry from
`expiry.get_batch_stock`. Nothing is cached: every call reflects the current ledger state.

Stock state per item (first match wins):
* **out**      — actual qty <= 0 (only for items stocked before in scope, or with a reorder level)
* **expired**  — holds expired batch quantity (must be quarantined/returned)
* **low**      — actual qty <= reorder level (sum of the in-scope warehouses' Item Reorder levels)
* **expiring** — nearest non-expired batch expires within the Expiring Soon window
* **ok**       — none of the above

Reorder suggestions use ERPNext's own rule (`erpnext.stock.reorder_item`): suggest when
projected qty < reorder level; quantity = max(reorder qty, reorder level - projected qty).
Projected qty already includes open purchase orders and material requests.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from pharmacyos_erp.permissions import require_pharmacy_role
from pharmacyos_erp.pharmacy.branches import get_permitted_warehouses, resolve_warehouses
from pharmacyos_erp.pharmacy.cost_privacy import can_see_costs, page_costs
from pharmacyos_erp.pharmacy.expiry import enrich, get_batch_stock, get_windows
from pharmacyos_erp.utils.arabic import normalize_arabic

STATES = ("out", "expired", "low", "expiring", "ok")


def _scope(branch, warehouse) -> list[str]:
	scope = resolve_warehouses(branch, warehouse)
	return get_permitted_warehouses() if scope is None else scope


def _items(search=None, medicines_only=False, item_codes=None):
	filters = {"disabled": 0, "is_stock_item": 1, "has_variants": 0}
	if medicines_only:
		filters["pharma_is_medicine"] = 1
	if item_codes is not None:
		filters["name"] = ["in", item_codes or [""]]
	or_filters = None
	if search:
		or_filters = {
			"name": ["like", f"%{search.strip()}%"],
			"pharma_search_key": ["like", f"%{normalize_arabic(search)}%"],
		}
	return {
		i.name: i
		for i in frappe.get_list(
			"Item",
			filters=filters,
			or_filters=or_filters,
			fields=["name", "item_name", "pharma_name_ar", "stock_uom", "item_group", "pharma_is_medicine"],
			limit_page_length=0,
		)
	}


def _bins(warehouses, item_codes=None):
	if not warehouses:
		return []
	filters = {"warehouse": ["in", warehouses]}
	if item_codes is not None:
		filters["item_code"] = ["in", item_codes or [""]]
	fields = ["item_code", "warehouse", "actual_qty", "reserved_qty", "projected_qty"]
	if can_see_costs():  # stock value and valuation only for cost-reading roles
		fields += ["stock_value", "valuation_rate"]
	return frappe.get_all("Bin", filters=filters, fields=fields)


def _reorder_rows(warehouses):
	if not warehouses:
		return []
	return frappe.get_all(
		"Item Reorder",
		filters={"parenttype": "Item", "warehouse": ["in", warehouses]},
		fields=[
			"parent as item_code",
			"warehouse",
			"warehouse_reorder_level",
			"warehouse_reorder_qty",
			"material_request_type",
		],
	)


@frappe.whitelist()
def get_inventory_health(
	branch: str | None = None,
	warehouse: str | None = None,
	state: str | None = None,
	search: str | None = None,
	medicines_only: int = 0,
	start: int = 0,
	page_length: int = 50,
) -> dict:
	require_pharmacy_role()
	if state and state not in STATES:
		frappe.throw(_("Unknown stock state."))
	page_length = min(max(cint(page_length), 1), 200)
	warehouses = _scope(branch, warehouse)
	items = _items(search, cint(medicines_only))

	agg = defaultdict(
		lambda: {
			"actual": 0.0,
			"reserved": 0.0,
			"projected": 0.0,
			"value": 0.0,
			"warehouses": 0,
			"has_bin": False,
		}
	)
	for b in _bins(warehouses):
		if b.item_code not in items:
			continue
		a = agg[b.item_code]
		a["actual"] += flt(b.actual_qty)
		a["reserved"] += flt(b.reserved_qty)
		a["projected"] += flt(b.projected_qty)
		a["value"] += flt(b.stock_value)
		a["has_bin"] = True
		if flt(b.actual_qty) > 0:
			a["warehouses"] += 1

	reorder = defaultdict(float)
	for r in _reorder_rows(warehouses):
		if r.item_code in items:
			reorder[r.item_code] += flt(r.warehouse_reorder_level)

	_critical, soon_days = get_windows()
	expiry = defaultdict(lambda: {"nearest": None, "nearest_days": None, "expired_qty": 0.0, "batches": 0})
	for r in enrich(get_batch_stock(warehouses)):
		if r["item_code"] not in items:
			continue
		e = expiry[r["item_code"]]
		e["batches"] += 1
		if r["days"] is not None and r["days"] < 0:
			e["expired_qty"] += r["qty"]
		elif r["days"] is not None and (e["nearest_days"] is None or r["days"] < e["nearest_days"]):
			e["nearest_days"], e["nearest"] = r["days"], r["expiry_date"]

	rows = []
	for code, item in items.items():
		a = agg.get(code)
		level = reorder.get(code, 0.0)
		if not a and not level:
			continue  # never stocked here and no reorder policy: not part of this inventory
		a = a or agg[code]
		e = expiry.get(code) or {"nearest": None, "nearest_days": None, "expired_qty": 0.0, "batches": 0}
		if a["actual"] <= 0:
			st = "out"
		elif e["expired_qty"] > 0:
			st = "expired"
		elif level and a["actual"] <= level:
			st = "low"
		elif e["nearest_days"] is not None and e["nearest_days"] <= soon_days:
			st = "expiring"
		else:
			st = "ok"
		rows.append(
			{
				"item_code": code,
				"item_name": item.item_name,
				"name_ar": item.pharma_name_ar,
				"uom": item.stock_uom,
				"is_medicine": cint(item.pharma_is_medicine),
				"qty": flt(a["actual"], 3),
				"available": flt(a["actual"] - a["reserved"], 3),
				"projected": flt(a["projected"], 3),
				"value": flt(a["value"], 2),
				"warehouses": a["warehouses"],
				"reorder_level": flt(level, 3),
				"batches": e["batches"],
				"expired_qty": flt(e["expired_qty"], 3),
				"nearest_expiry": e["nearest"],
				"nearest_days": e["nearest_days"],
				"state": st,
			}
		)

	counts = {s: 0 for s in STATES}
	for r in rows:
		counts[r["state"]] += 1
	selected = [r for r in rows if not state or r["state"] == state]
	order = {s: i for i, s in enumerate(STATES)}
	selected.sort(key=lambda r: (order[r["state"]], (r["item_name"] or r["item_code"]).lower()))
	return page_costs(
		{
			"rows": selected[cint(start) : cint(start) + page_length],
			"total": len(selected),
			"counts": counts,
			"inventory_value": flt(sum(r["value"] for r in rows), 2),
		}
	)


@frappe.whitelist()
def get_reorder_suggestions(
	branch: str | None = None, warehouse: str | None = None, search: str | None = None
) -> dict:
	require_pharmacy_role()
	warehouses = _scope(branch, warehouse)
	reorder_rows = _reorder_rows(warehouses)
	items = _items(search, item_codes=list({r.item_code for r in reorder_rows}))
	bins = {(b.item_code, b.warehouse): b for b in _bins(warehouses, list(items))}

	company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	suppliers = {}
	for d in frappe.get_all(
		"Item Default",
		filters={
			"parent": ["in", list(items) or [""]],
			"parenttype": "Item",
			"default_supplier": ["is", "set"],
		},
		fields=["parent", "company", "default_supplier"],
	):
		if d.parent not in suppliers or d.company == company:
			suppliers[d.parent] = d.default_supplier
	for d in frappe.get_all(
		"Item Supplier",
		filters={"parent": ["in", list(items) or [""]], "parenttype": "Item"},
		fields=["parent", "supplier"],
	):
		suppliers.setdefault(d.parent, d.supplier)

	rows = []
	for r in reorder_rows:
		item = items.get(r.item_code)
		if not item:
			continue
		b = bins.get((r.item_code, r.warehouse))
		projected = flt(b.projected_qty) if b else 0.0
		actual = flt(b.actual_qty) if b else 0.0
		level = flt(r.warehouse_reorder_level)
		if not level or projected >= level:
			continue
		qty = max(flt(r.warehouse_reorder_qty), level - projected)
		rows.append(
			{
				"item_code": r.item_code,
				"item_name": item.item_name,
				"name_ar": item.pharma_name_ar,
				"uom": item.stock_uom,
				"warehouse": r.warehouse,
				"qty": flt(actual, 3),
				"projected": flt(projected, 3),
				"reorder_level": level,
				"reorder_qty": flt(r.warehouse_reorder_qty),
				"suggested_qty": flt(qty, 3),
				"request_type": r.material_request_type or "Purchase",
				"supplier": suppliers.get(r.item_code),
				"est_rate": flt(b.valuation_rate) if b else 0.0,
			}
		)
	rows.sort(key=lambda x: (x["projected"] - x["reorder_level"], x["item_code"]))
	return page_costs({"rows": rows, "total": len(rows)})
