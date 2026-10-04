"""Catalog sync: published medicines/products, cursor-paginated by modification time.

The public price
----------------
The website is anonymous, so the only price it may show is the one ERPNext itself would charge a
customer it knows nothing about, today (N-05). `public_prices` applies ERPNext's own Item Price rules
(`erpnext.stock.get_item_details.get_item_price` for a transaction without a customer or supplier):

* the catalog's selling price list (Selling Settings → Default Price List), which must be enabled and
  a selling list; Item Price rows flagged as selling;
* no customer and no supplier — customer- and supplier-specific prices are never public, however
  cheap or recent;
* no batch — a batch-specific price applies to that batch at the counter, not to the product;
* the item's stock UOM (or no UOM): the catalog sells stock units;
* valid today (`valid_from` ≤ today ≤ `valid_upto`, missing bounds open), today being the ERP's own
  date (system time zone, Asia/Baghdad);
* among several valid rows, ERPNext's order: dated before undated, latest `valid_from`, stock UOM
  before blank, then name.

No such row → `price: null`: the website takes the product off sale. `price_valid_until` carries the
chosen row's `valid_upto`, so the Cloud stops selling at that price after that date by itself, even
if it does not hear from the ERP. Prices change state at midnight without any document being saved,
so `outbox.queue_price_validity_changes` sends the affected items when the date changes, and the
pull feed (`get_catalog`) versions an item by its price rows' validity boundaries as well.
"""

import frappe
from frappe.utils import cint, get_datetime, getdate, nowdate

from pharmacyos_erp.api.v1 import require_integration


def selling_price_list() -> str:
	"""The price list whose selling prices the website shows (Selling Settings → Default Price List)."""
	return frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"


def public_prices(item_codes, on_date=None) -> dict[str, frappe._dict]:
	"""{item_code: {rate, valid_until, currency}} — the currently valid public selling price only.

	The price ERPNext itself would charge for one unit at the counter today (its own ranking of the
	selling list's rows: dated rows first, latest start first), with nothing the website cannot honour:
	no customer-, supplier- or batch-specific row, no row in another UOM, and no row with a packing
	unit above one — ERPNext applies such a row only to quantities that are multiples of it, so a single
	unit has no price at all while it ranks first. Disabled items have no public price either: ERPNext
	refuses to sell them.
	"""
	codes = [c for c in item_codes if c]
	if not codes:
		return {}
	price_list = selling_price_list()
	listed = frappe.db.get_value("Price List", price_list, ["enabled", "selling", "currency"], as_dict=True)
	if not listed or not cint(listed.enabled) or not cint(listed.selling):
		return {}
	today = getdate(on_date or nowdate())
	rows = frappe.db.sql(
		"""
		select ip.name, ip.item_code, ip.price_list_rate, ip.uom, ip.valid_from, ip.valid_upto,
			ip.packing_unit
		from `tabItem Price` ip
		join `tabItem` i on i.name = ip.item_code and ifnull(i.disabled, 0) = 0
		where ip.item_code in %(codes)s
			and ip.price_list = %(price_list)s
			and ip.selling = 1
			and ifnull(ip.customer, '') = ''
			and ifnull(ip.supplier, '') = ''
			and ifnull(ip.batch_no, '') = ''
			and ifnull(ip.uom, '') in ('', i.stock_uom)
			and ifnull(ip.valid_from, '2000-01-01') <= %(today)s
			and ifnull(ip.valid_upto, '2500-12-31') >= %(today)s
		""",
		{"codes": tuple(codes), "price_list": price_list, "today": today},
		as_dict=True,
	)

	def rank(row):  # ERPNext's order (get_item_price), best first
		dated = row.valid_from is not None
		return (dated, row.valid_from or getdate("1900-01-01"), row.uom or "", row.name)

	best = {}
	for row in sorted(rows, key=rank, reverse=True):
		best.setdefault(row.item_code, row)
	# ERPNext's choice applies only to multiples of its packing unit: not a per-unit public price
	best = {code: row for code, row in best.items() if cint(row.packing_unit) <= 1}
	return {
		code: frappe._dict(
			rate=row.price_list_rate,
			valid_until=str(row.valid_upto) if row.valid_upto else None,
			currency=listed.currency,
		)
		for code, row in best.items()
	}


@frappe.whitelist(methods=["GET"])
def get_catalog(modified_since: str | None = None, cursor: str | None = None, limit: int = 200) -> dict:
	"""Items with `pharmacyos_publish = 1`, oldest change (item or selling price) first.

	Pass the returned `next_cursor` back as `cursor` to continue; `has_more` is false at the end.
	Disabled items are returned with `disabled: 1` so the storefront can unpublish them.
	"""
	require_integration()
	limit = min(max(cint(limit), 1), 500)

	# A product's catalog version is the later of its Item and its selling Item Prices: a price change
	# alone (no Item save) moves the product forward in the feed, and so does a price row starting or
	# ending its validity (the start of `valid_from`, the day after `valid_upto`) once that moment has
	# passed — nothing is saved at midnight. Keyset pagination on (version, name).
	params = {"price_list": selling_price_list(), "limit": limit + 1, "today": getdate(nowdate())}
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
			select it.name, greatest(it.modified, coalesce(max(greatest(
				ip.modified,
				coalesce(if(ip.valid_from <= %(today)s, timestamp(ip.valid_from), null), ip.modified),
				coalesce(
					if(ip.valid_upto < %(today)s, timestamp(date_add(ip.valid_upto, interval 1 day)), null),
					ip.modified
				)
			)), it.modified)) as version
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
	"""Public catalog rows (no costs, no stock ledger details, only the public price)."""
	codes = [r.name for r in rows] or [""]
	prices = public_prices(codes)
	currency = frappe.db.get_value("Price List", selling_price_list(), "currency")
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
			"price": prices[r.name].rate if r.name in prices else None,
			# last day (ERP date) this price is valid; the website stops selling at it afterwards
			"price_valid_until": prices[r.name].valid_until if r.name in prices else None,
			"currency": currency,
			"disabled": cint(r.disabled),
			"modified": str(r.modified),
		}
		for r in rows
	]


def catalog_item(item_code: str) -> dict | None:
	rows = frappe.get_all("Item", filters={"name": item_code}, fields=CATALOG_FIELDS)
	return serialize_items(rows)[0] if rows else None
