"""Catalog sync: published medicines/products, cursor-paginated by modification time."""

import frappe
from frappe.utils import cint, get_datetime

from pharmacyos_erp.api.v1 import require_integration


def selling_price_list() -> str:
	"""The price list whose selling prices the website shows (Selling Settings → Default Price List)."""
	return frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"


@frappe.whitelist(methods=["GET"])
def get_catalog(modified_since: str | None = None, cursor: str | None = None, limit: int = 200) -> dict:
	"""Items with `pharmacyos_publish = 1`, oldest change (item or selling price) first.

	Pass the returned `next_cursor` back as `cursor` to continue; `has_more` is false at the end.
	Disabled items are returned with `disabled: 1` so the storefront can unpublish them.
	"""
	require_integration()
	limit = min(max(cint(limit), 1), 500)

	# A product's catalog version is the later of its Item and its selling Item Price: a price change
	# alone (no Item save) moves the product forward in the feed. Keyset pagination on (version, name).
	params = {"price_list": selling_price_list(), "limit": limit + 1}
	conditions = ["i.pharmacyos_publish = 1"]
	if cursor:
		mod, _sep, last_name = cursor.partition("|")
		params.update({"mod": get_datetime(mod), "last_name": last_name})
		conditions.append("(v.version > %(mod)s or (v.version = %(mod)s and i.name > %(last_name)s))")
	elif modified_since:
		params["since"] = get_datetime(modified_since)
		conditions.append("v.version >= %(since)s")
	columns = ", ".join(f"i.`{f}`" for f in CATALOG_FIELDS if f != "modified")
	rows = frappe.db.sql(
		f"""
		select {columns}, v.version as modified
		from `tabItem` i
		join (
			select it.name, greatest(it.modified, coalesce(max(ip.modified), it.modified)) as version
			from `tabItem` it
			left join `tabItem Price` ip
				on ip.item_code = it.name and ip.price_list = %(price_list)s and ip.selling = 1
			where it.pharmacyos_publish = 1
			group by it.name, it.modified
		) v on v.name = i.name
		where {" and ".join(conditions)}
		order by v.version, i.name
		limit %(limit)s
		""",
		params,
		as_dict=True,
	)
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
			filters={"item_code": ["in", codes], "price_list": selling_price_list(), "selling": 1},
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
			"currency": frappe.db.get_value("Price List", selling_price_list(), "currency"),
			"disabled": cint(r.disabled),
			"modified": str(r.modified),
		}
		for r in rows
	]


def catalog_item(item_code: str) -> dict | None:
	rows = frappe.get_all("Item", filters={"name": item_code}, fields=CATALOG_FIELDS)
	return serialize_items(rows)[0] if rows else None
