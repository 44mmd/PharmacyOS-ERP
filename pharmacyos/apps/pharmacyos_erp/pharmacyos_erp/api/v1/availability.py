"""Sellable availability per branch.

Sellable quantity for a published item in a branch =
* batch-tracked: sum of **non-expired** batch quantities (ERPNext's own batch availability query,
  which already excludes expired batches) in the branch's warehouses,
* otherwise: on-hand quantity,
minus quantity reserved by open Sales Orders (Bin.reserved_qty), never below zero.
"""

import frappe
from frappe.utils import cint, flt

from pharmacyos_erp.api.v1 import get_branch_by_code, require_integration
from pharmacyos_erp.pharmacy.branches import get_branch_warehouses


def sellable_qty(item_code: str, warehouses: list[str], has_batch_no: bool) -> tuple[float, str | None]:
	if not warehouses:
		return 0.0, None
	nearest = None
	if has_batch_no:
		from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_auto_batch_nos

		batches = get_auto_batch_nos(
			frappe._dict(
				{
					"item_code": item_code,
					"warehouse": warehouses,
					"based_on": "Expiry",
					"ignore_reserved_stock": True,
					"for_stock_levels": False,
				}
			)
		)
		on_hand = sum(flt(b.get("qty")) for b in batches if flt(b.get("qty")) > 0)
		expiries = [b.get("expiry_date") for b in batches if b.get("expiry_date") and flt(b.get("qty")) > 0]
		nearest = str(min(expiries)) if expiries else None
	else:
		on_hand = flt(
			frappe.get_all(
				"Bin",
				filters={"item_code": item_code, "warehouse": ["in", warehouses]},
				fields=[{"SUM": "actual_qty", "as": "q"}],
			)[0].q
		)
	reserved = flt(
		frappe.get_all(
			"Bin",
			filters={"item_code": item_code, "warehouse": ["in", warehouses]},
			fields=[{"SUM": "reserved_qty", "as": "q"}],
		)[0].q
	)
	return max(0.0, flt(on_hand - reserved, 3)), nearest


@frappe.whitelist(methods=["GET"])
def get_availability(branch_code: str | None = None, item_codes: str | list | None = None) -> dict:
	"""Availability of published items, per branch (all storefront branches when no code given)."""
	require_integration()
	if isinstance(item_codes, str):
		item_codes = (
			frappe.parse_json(item_codes)
			if item_codes.strip().startswith("[")
			else [c.strip() for c in item_codes.split(",") if c.strip()]
		)

	if branch_code:
		branches = [get_branch_by_code(branch_code)]
		branches[0]["code"] = branch_code
	else:
		branches = frappe.get_all(
			"Branch",
			filters={"pharmacyos_storefront_code": ["is", "set"], "pharmacyos_warehouse": ["is", "set"]},
			fields=["name", "pharmacyos_warehouse", "pharmacyos_storefront_code as code"],
		)

	item_filters = {"pharmacyos_publish": 1, "disabled": 0}
	if item_codes:
		item_filters["name"] = ["in", item_codes]
	items = frappe.get_all(
		"Item", filters=item_filters, fields=["name", "has_batch_no"], limit_page_length=2000
	)

	out = []
	for b in branches:
		warehouses = get_branch_warehouses(b["name"])
		for item in items:
			qty, nearest = sellable_qty(item.name, warehouses, cint(item.has_batch_no))
			out.append(
				{"branch_code": b["code"], "item_code": item.name, "qty": qty, "nearest_expiry": nearest}
			)
	return {"availability": out}
