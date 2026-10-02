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

from pharmacyos_erp.utils.arabic import build_search_key, normalize_arabic


def validate_item(doc, method=None):
	doc.pharma_search_key = compute_search_key(doc)
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


def compute_search_key(doc) -> str:
	"""Normalised names, codes, barcodes and ingredients (Arabic and English) for tolerant search."""
	ingredients = [
		row.active_ingredient for row in doc.get("pharma_ingredients") or [] if row.active_ingredient
	]
	ingredient_ar = (
		frappe.get_all("Active Ingredient", filters={"name": ["in", ingredients]}, pluck="ingredient_name_ar")
		if ingredients
		else []
	)
	return build_search_key(
		doc.get("item_code") or doc.name,
		doc.get("item_name"),
		doc.get("pharma_name_ar"),
		doc.get("pharma_generic_name"),
		*ingredients,
		*ingredient_ar,
		*(row.barcode for row in doc.get("barcodes") or []),
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


@frappe.whitelist()
def search_medicines(query: str, limit: int = 20, medicines_only: int = 0) -> list[dict]:
	"""Arabic-tolerant search across Arabic/English/generic names, SKU, barcode and ingredients.

	An exact barcode match comes first (scanner input); then items whose normalised search key
	contains the normalised query (أ/إ/آ → ا, ى → ي, ة → ه, harakat ignored). Uses the caller's
	Item permissions.
	"""
	query = (query or "").strip()
	if not query:
		return []
	limit = min(max(cint(limit), 1), 100)
	fields = [
		"name",
		"item_name",
		"pharma_name_ar",
		"pharma_generic_name",
		"pharma_strength",
		"pharma_dosage_form",
		"stock_uom",
		"pharma_is_medicine",
	]
	filters = {"disabled": 0}
	if cint(medicines_only):
		filters["pharma_is_medicine"] = 1

	results, seen = [], set()
	barcode_item = frappe.db.get_value("Item Barcode", {"barcode": query}, "parent")
	if barcode_item:
		for row in frappe.get_list("Item", filters={**filters, "name": barcode_item}, fields=fields):
			row["matched_barcode"] = query
			results.append(row)
			seen.add(row.name)

	needle = normalize_arabic(query)
	if needle:
		for row in frappe.get_list(
			"Item",
			filters={**filters, "pharma_search_key": ["like", f"%{needle}%"]},
			fields=fields,
			order_by="item_name asc",
			limit_page_length=limit,
		):
			if row.name not in seen:
				results.append(row)
				seen.add(row.name)
	return results[:limit]
