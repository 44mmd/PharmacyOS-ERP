"""Catalog sync: published medicines/products, cursor-paginated by modification time."""

import frappe
from frappe.utils import cint, get_datetime

from pharmacyos_erp.api.v1 import require_integration


def _selling_price_list() -> str:
	return frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"


@frappe.whitelist(methods=["GET"])
def get_catalog(modified_since: str | None = None, cursor: str | None = None, limit: int = 200) -> dict:
	"""Items with `pharmacyos_publish = 1`, oldest change first.

	Pass the returned `next_cursor` back as `cursor` to continue; `has_more` is false at the end.
	Disabled items are returned with `disabled: 1` so the storefront can unpublish them.
	"""
	require_integration()
	limit = min(max(cint(limit), 1), 500)

	fields = [
		"name",
		"item_name",
		"pharma_name_ar",
		"pharma_generic_name",
		"pharma_strength",
		"pharma_dosage_form",
		"pharma_pack_size",
		"pharma_dispensing",
		"pharma_is_medicine",
		"brand",
		"item_group",
		"stock_uom",
		"image",
		"disabled",
		"modified",
	]
	item = frappe.qb.DocType("Item")
	query = frappe.qb.from_(item).select(*[item[f] for f in fields]).where(item.pharmacyos_publish == 1)
	if cursor:
		# keyset pagination on (modified, name): exact even when many rows share a timestamp
		mod, _sep, last_name = cursor.partition("|")
		mod = get_datetime(mod)
		query = query.where((item.modified > mod) | ((item.modified == mod) & (item.name > last_name)))
	elif modified_since:
		query = query.where(item.modified >= get_datetime(modified_since))
	rows = query.orderby(item.modified).orderby(item.name).limit(limit + 1).run(as_dict=True)
	has_more = len(rows) > limit
	rows = rows[:limit]

	items = serialize_items(rows)
	next_cursor = f"{rows[-1].modified}|{rows[-1].name}" if rows else cursor
	return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


CATALOG_FIELDS = [
	"name",
	"item_name",
	"pharma_name_ar",
	"pharma_generic_name",
	"pharma_strength",
	"pharma_dosage_form",
	"pharma_pack_size",
	"pharma_dispensing",
	"pharma_is_medicine",
	"brand",
	"item_group",
	"stock_uom",
	"image",
	"disabled",
	"modified",
]


def serialize_items(rows) -> list[dict]:
	"""Public catalog rows (no costs, no stock ledger details)."""
	codes = [r.name for r in rows] or [""]
	prices = dict(
		frappe.get_all(
			"Item Price",
			filters={"item_code": ["in", codes], "price_list": _selling_price_list(), "selling": 1},
			fields=["item_code", "price_list_rate"],
			as_list=True,
		)
	)
	barcodes = {}
	for b in frappe.get_all("Item Barcode", filters={"parent": ["in", codes]}, fields=["parent", "barcode"]):
		barcodes.setdefault(b.parent, []).append(b.barcode)

	return [
		{
			"item_code": r.name,
			"name": r.item_name,
			"name_ar": r.pharma_name_ar,
			"generic_name": r.pharma_generic_name,
			"strength": r.pharma_strength,
			"dosage_form": r.pharma_dosage_form,
			"pack_size": r.pharma_pack_size,
			"dispensing": r.pharma_dispensing,
			"is_medicine": cint(r.pharma_is_medicine),
			"brand": r.brand,
			"category": r.item_group,
			"uom": r.stock_uom,
			"image": r.image,
			"barcodes": barcodes.get(r.name, []),
			"price": prices.get(r.name),
			"currency": frappe.db.get_value("Price List", _selling_price_list(), "currency"),
			"disabled": cint(r.disabled),
			"modified": str(r.modified),
		}
		for r in rows
	]


def catalog_item(item_code: str) -> dict | None:
	rows = frappe.get_all("Item", filters={"name": item_code}, fields=CATALOG_FIELDS)
	return serialize_items(rows)[0] if rows else None
