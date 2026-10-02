"""Medicine layer over ERPNext's Item.

A medicine *is* an ERPNext Item with `pharma_is_medicine = 1`; nothing about Item's internals, naming
or stock behaviour is changed. This module adds:

* validation that only applies to medicines (batch + expiry tracking guidance, strength summary);
* `create_medicine`: the pharmacy-friendly creation path used by the "Add Medicine" dialog. It
  creates the Item (and optional buying price) through normal document APIs, so permissions,
  naming, validation and audit trail all stay ERPNext's.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt


def validate_item(doc, method=None):
	if not doc.get("pharma_is_medicine"):
		return

	# Keep the summary strength in step with a single-ingredient medicine.
	ingredients = doc.get("pharma_ingredients") or []
	if not doc.get("pharma_strength") and len(ingredients) == 1 and ingredients[0].strength:
		doc.pharma_strength = ingredients[0].strength

	if doc.get("is_stock_item") and not (doc.get("has_batch_no") and doc.get("has_expiry_date")):
		frappe.msgprint(
			_(
				"Medicines are normally tracked by batch and expiry date. Enable <b>Has Batch No</b> and <b>Has Expiry Date</b> so PharmacyOS can apply expiry checks and FEFO."
			),
			title=_("Batch & Expiry Tracking"),
			indicator="orange",
			alert=True,
		)


@frappe.whitelist(methods=["POST"])
def create_medicine(
	item_name: str,
	item_group: str,
	stock_uom: str,
	item_code: str | None = None,
	pharma_name_ar: str | None = None,
	pharma_generic_name: str | None = None,
	pharma_strength: str | None = None,
	pharma_dosage_form: str | None = None,
	pharma_pack_size: str | None = None,
	pharma_dispensing: str | None = None,
	brand: str | None = None,
	pharma_manufacturer: str | None = None,
	barcode: str | None = None,
	active_ingredient: str | None = None,
	standard_rate: float | None = None,
	buying_rate: float | None = None,
	shelf_life_in_days: int | None = None,
	reorder_warehouse: str | None = None,
	reorder_level: float | None = None,
	reorder_qty: float | None = None,
	default_supplier: str | None = None,
) -> str:
	"""Create a batch- and expiry-tracked medicine. Returns the Item name."""
	if not frappe.has_permission("Item", "create"):
		frappe.throw(_("You are not allowed to create medicines."), frappe.PermissionError)

	item = frappe.new_doc("Item")
	item.update(
		{
			"item_code": (item_code or "").strip() or None,
			"item_name": item_name.strip(),
			"item_group": item_group,
			"stock_uom": stock_uom,
			"is_stock_item": 1,
			"include_item_in_manufacturing": 0,
			"has_batch_no": 1,
			"create_new_batch": 0,  # the supplier's printed batch number is entered on receipt
			"has_expiry_date": 1,
			"shelf_life_in_days": cint(shelf_life_in_days) or None,
			"pharma_is_medicine": 1,
			"pharma_name_ar": pharma_name_ar,
			"pharma_generic_name": pharma_generic_name,
			"pharma_strength": pharma_strength,
			"pharma_dosage_form": pharma_dosage_form,
			"pharma_pack_size": pharma_pack_size,
			"pharma_dispensing": pharma_dispensing,
			"pharma_manufacturer": pharma_manufacturer,
			"brand": brand,
			"standard_rate": flt(standard_rate) or None,
		}
	)
	if not item.item_code:
		# Follow the site's naming setting; if it is "Item Code", generate a readable code.
		if frappe.db.get_single_value("Stock Settings", "item_naming_by") != "Naming Series":
			item.item_code = frappe.model.naming.make_autoname("MED-.#####", "Item")
	if active_ingredient:
		item.append(
			"pharma_ingredients", {"active_ingredient": active_ingredient, "strength": pharma_strength}
		)
	if barcode:
		item.append("barcodes", {"barcode": barcode.strip()})
	if reorder_warehouse and flt(reorder_level):
		item.append(
			"reorder_levels",
			{
				"warehouse": reorder_warehouse,
				"warehouse_reorder_level": flt(reorder_level),
				"warehouse_reorder_qty": flt(reorder_qty) or flt(reorder_level),
				"material_request_type": "Purchase",
			},
		)
	if default_supplier:
		company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)
		if company:
			item.append("item_defaults", {"company": company, "default_supplier": default_supplier})
	item.insert()

	if flt(buying_rate):
		price_list = frappe.db.get_single_value("Buying Settings", "buying_price_list") or "Standard Buying"
		if frappe.db.exists("Price List", price_list) and frappe.has_permission("Item Price", "create"):
			frappe.get_doc(
				{
					"doctype": "Item Price",
					"item_code": item.name,
					"price_list": price_list,
					"price_list_rate": flt(buying_rate),
				}
			).insert()

	return item.name
