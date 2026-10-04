"""Batch & expiry engine (read-only).

Batch quantities come from ERPNext's own batch availability query
(`get_auto_batch_nos` with `for_stock_levels=True`, which also returns expired batches and covers
both Serial-and-Batch-Bundle and legacy `batch_no` ledger entries). PharmacyOS never re-derives
stock from the ledger itself and never writes to it.

Value at risk is an estimate: batch quantity x the item's current valuation rate in that warehouse
(Bin). The UI labels it as such.

The pharmacist-facing batch number is `Batch.batch_id`. `Batch.name` (a random hash in v17) is only
used for links and never displayed.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, nowdate

from pharmacyos_erp.permissions import require_pharmacy_role
from pharmacyos_erp.pharmacy.branches import get_permitted_warehouses, resolve_warehouses
from pharmacyos_erp.pharmacy.cost_privacy import can_see_costs, page_costs
from pharmacyos_erp.utils.arabic import normalize_arabic

BUCKETS = ("expired", "30", "60", "90", "all")


def get_windows() -> tuple[int, int]:
	settings = frappe.get_cached_doc("PharmacyOS Settings")
	return cint(settings.critical_days) or 30, cint(settings.expiring_soon_days) or 90


def classify(expiry_date, today=None, critical_days=30, soon_days=90) -> tuple[str, int | None]:
	"""Return (status, days_to_expiry). Status: expired | critical | soon | healthy | none."""
	if not expiry_date:
		return "none", None
	days = date_diff(getdate(expiry_date), getdate(today or nowdate()))
	if days < 0:
		return "expired", days
	if days <= critical_days:
		return "critical", days
	if days <= soon_days:
		return "soon", days
	return "healthy", days


def in_bucket(bucket: str, days: int | None) -> bool:
	if bucket in (None, "", "all"):
		return True
	if days is None:
		return False
	if bucket == "expired":
		return days < 0
	return 0 <= days <= cint(bucket)


def get_batch_stock(warehouses: list[str] | None = None, item_code: str | None = None) -> list[dict]:
	"""Positive batch balances per (batch, warehouse), including expired batches.

	`warehouses=None` means every warehouse the current user may read.
	"""
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_auto_batch_nos

	if warehouses is None:
		warehouses = get_permitted_warehouses()
	if not warehouses:
		return []

	rows = get_auto_batch_nos(
		frappe._dict(
			{
				"warehouse": warehouses,
				"item_code": item_code,
				"for_stock_levels": True,
				"ignore_reserved_stock": True,
				"based_on": "Expiry",
			}
		)
	)
	precision = frappe.get_precision("Stock Ledger Entry", "actual_qty") or 3
	return [r for r in rows if flt(r.get("qty"), precision) > 0]


def enrich(rows: list[dict]) -> list[dict]:
	"""Attach batch, item and valuation details; classify expiry."""
	if not rows:
		return []
	batch_names = list({r["batch_no"] for r in rows})
	batches = {
		b.name: b
		for b in frappe.get_all(
			"Batch",
			filters={"name": ["in", batch_names]},
			fields=[
				"name",
				"batch_id",
				"item",
				"expiry_date",
				"manufacturing_date",
				"supplier",
				"reference_doctype",
				"reference_name",
			],
		)
	}
	item_codes = list({b.item for b in batches.values()})
	items = {
		i.name: i
		for i in frappe.get_all(
			"Item",
			filters={"name": ["in", item_codes]},
			fields=[
				"name",
				"item_name",
				"pharma_name_ar",
				"pharma_generic_name",
				"stock_uom",
				"pharma_is_medicine",
				"item_group",
			],
		)
	}
	warehouses = list({r["warehouse"] for r in rows})
	# no valuation at all without cost access: nothing to sort or rank by, nothing to send
	valuation = (
		{}
		if not can_see_costs()
		else {
			(b.item_code, b.warehouse): flt(b.valuation_rate)
			for b in frappe.get_all(
				"Bin",
				filters={"item_code": ["in", item_codes], "warehouse": ["in", warehouses]},
				fields=["item_code", "warehouse", "valuation_rate"],
			)
		}
	)

	critical_days, soon_days = get_windows()
	today = nowdate()
	out = []
	for r in rows:
		batch = batches.get(r["batch_no"])
		if not batch:
			continue
		item = items.get(batch.item) or frappe._dict()
		status, days = classify(batch.expiry_date, today, critical_days, soon_days)
		qty = flt(r["qty"])
		rate = valuation.get((batch.item, r["warehouse"]), 0.0)
		out.append(
			{
				"batch": batch.name,
				"batch_id": batch.batch_id,
				"item_code": batch.item,
				"item_name": item.get("item_name"),
				"name_ar": item.get("pharma_name_ar"),
				"generic_name": item.get("pharma_generic_name"),
				"is_medicine": cint(item.get("pharma_is_medicine")),
				"item_group": item.get("item_group"),
				"uom": item.get("stock_uom"),
				"warehouse": r["warehouse"],
				"qty": qty,
				"expiry_date": batch.expiry_date,
				"manufacturing_date": batch.manufacturing_date,
				"days": days,
				"status": status,
				"supplier": batch.supplier,
				"source_doctype": batch.reference_doctype,
				"source_name": batch.reference_name,
				"valuation_rate": rate,
				"value": flt(qty * rate, 2),
			}
		)
	return out


def filter_rows(rows, search=None, medicines_only=False):
	if medicines_only:
		rows = [r for r in rows if r["is_medicine"]]
	if search:
		needle = normalize_arabic(search)
		rows = [
			r
			for r in rows
			if any(
				needle in normalize_arabic(r.get(k))
				for k in ("batch_id", "item_code", "item_name", "name_ar", "generic_name")
			)
		]
	return rows


@frappe.whitelist()
def get_batches(
	bucket: str = "all",
	branch: str | None = None,
	warehouse: str | None = None,
	search: str | None = None,
	medicines_only: int = 0,
	start: int = 0,
	page_length: int = 50,
) -> dict:
	"""Batches & Expiry page: batch balances with expiry status, bucket counts and value at risk."""
	require_pharmacy_role()
	if bucket not in BUCKETS:
		frappe.throw(_("Unknown expiry bucket."))
	page_length = min(max(cint(page_length), 1), 200)
	start = max(cint(start), 0)

	rows = enrich(get_batch_stock(resolve_warehouses(branch, warehouse)))
	rows = filter_rows(rows, search, cint(medicines_only))

	counts = {b: 0 for b in BUCKETS}
	value = {b: 0.0 for b in BUCKETS}
	for r in rows:
		for b in BUCKETS:
			if in_bucket(b, r["days"]):
				counts[b] += 1
				value[b] += r["value"]

	selected = [r for r in rows if in_bucket(bucket, r["days"])]
	selected.sort(
		key=lambda r: (r["days"] is None, r["days"] if r["days"] is not None else 0, r["item_code"])
	)
	return page_costs(
		{
			"rows": selected[start : start + page_length],
			"total": len(selected),
			"counts": counts,
			"value": {k: flt(v, 2) for k, v in value.items()},
			"windows": dict(zip(("critical_days", "expiring_soon_days"), get_windows(), strict=True)),
		}
	)


@frappe.whitelist()
def get_expiry_intelligence(
	branch: str | None = None, warehouse: str | None = None, horizon: int = 90
) -> dict:
	"""Aggregated expiry risk: buckets, warehouses, suppliers and top medicines by value at risk."""
	require_pharmacy_role()
	horizon = cint(horizon) if cint(horizon) in (0, 30, 60, 90) else 90
	rows = enrich(get_batch_stock(resolve_warehouses(branch, warehouse)))

	bucket_defs = [
		("expired", _("Expired"), lambda d: d is not None and d < 0),
		("0_30", _("0–30 days"), lambda d: d is not None and 0 <= d <= 30),
		("31_60", _("31–60 days"), lambda d: d is not None and 31 <= d <= 60),
		("61_90", _("61–90 days"), lambda d: d is not None and 61 <= d <= 90),
		("over_90", _("Over 90 days"), lambda d: d is not None and d > 90),
		("no_expiry", _("No expiry date"), lambda d: d is None),
	]
	buckets = []
	for key, label, test in bucket_defs:
		matched = [r for r in rows if test(r["days"])]
		buckets.append(
			{
				"key": key,
				"label": label,
				"batches": len(matched),
				"qty": flt(sum(r["qty"] for r in matched), 3),
				"value": flt(sum(r["value"] for r in matched), 2),
			}
		)

	# "at risk" = expired, or expiring within the selected horizon (0 = expired only)
	def at_risk(r):
		return r["days"] is not None and (r["days"] < 0 or r["days"] <= horizon)

	risk = [r for r in rows if at_risk(r)]

	def group(key_fn, limit=10):
		agg = defaultdict(lambda: {"batches": 0, "qty": 0.0, "value": 0.0, "expired_value": 0.0})
		for r in risk:
			k = key_fn(r)
			agg[k]["batches"] += 1
			agg[k]["qty"] += r["qty"]
			agg[k]["value"] += r["value"]
			if r["days"] < 0:
				agg[k]["expired_value"] += r["value"]
		out = [{"key": k, **{m: flt(v, 2) for m, v in vals.items()}} for k, vals in agg.items()]
		out.sort(key=lambda x: x["value"], reverse=True)
		return out[:limit]

	by_item = group(lambda r: r["item_code"])
	names = {r["item_code"]: (r["item_name"], r["name_ar"]) for r in risk}
	for row in by_item:
		row["item_name"], row["name_ar"] = names.get(row["key"], (None, None))

	return page_costs(
		{
			"horizon": horizon,
			"buckets": buckets,
			"total_value": flt(sum(r["value"] for r in rows), 2),
			"risk_value": flt(sum(r["value"] for r in risk), 2),
			"risk_batches": len(risk),
			"by_warehouse": group(lambda r: r["warehouse"]),
			"by_supplier": group(lambda r: r["supplier"] or ""),
			"by_item": by_item,
			"batches": sorted(risk, key=lambda r: r["days"])[:100],
		}
	)


@frappe.whitelist()
def get_item_expiry_summary(item_code: str) -> dict:
	"""Compact per-medicine summary for the Item form."""
	if not frappe.has_permission("Item", "read", item_code):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	rows = enrich(get_batch_stock(None, item_code))
	summary = {s: 0 for s in ("expired", "critical", "soon", "healthy", "none")}
	for r in rows:
		summary[r["status"]] += 1
	nearest = min(
		(r for r in rows if r["days"] is not None and r["days"] >= 0), key=lambda r: r["days"], default=None
	)
	return {
		"batches": len(rows),
		"status_counts": summary,
		"expired_qty": flt(sum(r["qty"] for r in rows if r["status"] == "expired"), 3),
		"nearest": {k: nearest[k] for k in ("batch_id", "expiry_date", "days", "warehouse", "qty")}
		if nearest
		else None,
	}
