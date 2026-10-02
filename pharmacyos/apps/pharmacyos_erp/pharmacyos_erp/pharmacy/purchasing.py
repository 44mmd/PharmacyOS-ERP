"""Purchasing helpers. Documents are created as ERPNext drafts through normal APIs (permissions,
item-detail fetching, validation and audit stay ERPNext's); a person reviews and submits them."""

import json

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate


@frappe.whitelist(methods=["POST"])
def make_purchase_order(supplier: str, items: str | list, company: str | None = None) -> str:
	"""Create a draft Purchase Order from reorder suggestions. Returns its name.

	`items`: [{"item_code", "qty", "warehouse"}]
	"""
	if isinstance(items, str):
		items = json.loads(items)
	if not items:
		frappe.throw(_("Select at least one medicine to order."))
	if not frappe.has_permission("Purchase Order", "create"):
		frappe.throw(_("You are not allowed to create purchase orders."), frappe.PermissionError)

	company = (
		company
		or frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)
	schedule_date = add_days(nowdate(), 7)
	po = frappe.new_doc("Purchase Order")
	po.update(
		{
			"supplier": supplier,
			"company": company,
			"transaction_date": nowdate(),
			"schedule_date": schedule_date,
		}
	)
	for row in items:
		qty = flt(row.get("qty"))
		if qty <= 0:
			continue
		po.append(
			"items",
			{
				"item_code": row["item_code"],
				"qty": qty,
				"warehouse": row.get("warehouse"),
				"schedule_date": schedule_date,
			},
		)
	if not po.items:
		frappe.throw(_("Nothing to order."))
	po.set_missing_values()
	po.insert()
	return po.name
