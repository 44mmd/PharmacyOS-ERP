"""FEFO (First Expiry, First Out) guidance for sales of medicines.

What ERPNext already does (and PharmacyOS relies on):
* `Stock Settings.pick_serial_and_batch_based_on = "Expiry"` (set by PharmacyOS) makes ERPNext's
  automatic batch selection pick the earliest-expiring batch first.
* Expired batches are excluded from automatic selection and rejected on sale (`BatchExpiredError`).

What PharmacyOS adds: when a user *manually* picks a batch that expires later than another
non-expired batch still available in the same warehouse, it warns (or blocks, per
PharmacyOS Settings → FEFO Guidance). It only reads stock; it never changes the chosen batch, the
ledger or valuation.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

SALES_DOCTYPES = ("Sales Invoice", "POS Invoice", "Delivery Note")


def get_fefo_available(item_code: str, warehouse: str) -> list[dict]:
	"""Non-expired batches with stock, earliest expiry first (ERPNext's own query)."""
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_auto_batch_nos

	rows = get_auto_batch_nos(
		frappe._dict(
			{
				"item_code": item_code,
				"warehouse": warehouse,
				"based_on": "Expiry",
				"ignore_reserved_stock": True,
				"for_stock_levels": False,  # excludes expired batches
			}
		)
	)
	merged = defaultdict(float)
	expiry = {}
	for r in rows:
		merged[r["batch_no"]] += flt(r["qty"])
		expiry[r["batch_no"]] = r.get("expiry_date")
	out = [{"batch_no": b, "qty": q, "expiry_date": expiry[b]} for b, q in merged.items() if q > 0]
	out.sort(key=lambda r: getdate(r["expiry_date"]) if r["expiry_date"] else getdate("9999-12-31"))
	return out


@frappe.whitelist()
def get_fefo_batches(item_code: str, warehouse: str) -> list[dict]:
	"""FEFO-ordered batches for UI hints (batch_id shown, never the internal name)."""
	if not frappe.has_permission("Item", "read", item_code) or not frappe.has_permission(
		"Warehouse", "read", warehouse
	):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	rows = get_fefo_available(item_code, warehouse)
	ids = dict(
		frappe.get_all(
			"Batch",
			filters={"name": ["in", [r["batch_no"] for r in rows] or [""]]},
			fields=["name", "batch_id"],
			as_list=True,
		)
	)
	for r in rows:
		r["batch_id"] = ids.get(r["batch_no"])
	return rows


def get_row_batches(doc, row) -> dict[str, float]:
	"""Batch -> qty used by an item row (batch_no field or its Serial and Batch Bundle)."""
	if row.get("batch_no"):
		return {row.batch_no: abs(flt(row.get("stock_qty") or row.get("qty")))}
	if row.get("serial_and_batch_bundle"):
		entries = frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": row.serial_and_batch_bundle},
			fields=["batch_no", "qty"],
		)
		used = defaultdict(float)
		for e in entries:
			if e.batch_no:
				used[e.batch_no] += abs(flt(e.qty))
		return dict(used)
	return {}


def find_fefo_deviations(doc) -> list[dict]:
	"""Rows whose chosen batch skips an earlier-expiring batch that is still available."""
	if doc.get("is_return"):
		return []
	if doc.doctype == "Sales Invoice" and not doc.get("update_stock"):
		return []

	medicine_items = set(
		frappe.get_all(
			"Item",
			filters={
				"name": ["in", list({r.item_code for r in doc.items}) or [""]],
				"pharma_is_medicine": 1,
				"has_batch_no": 1,
			},
			pluck="name",
		)
	)
	if not medicine_items:
		return []

	used = defaultdict(lambda: defaultdict(float))  # (item, warehouse) -> batch -> qty in this document
	rows_by_key = defaultdict(list)
	for row in doc.items:
		if row.item_code not in medicine_items or not row.get("warehouse"):
			continue
		key = (row.item_code, row.warehouse)
		for batch, qty in get_row_batches(doc, row).items():
			used[key][batch] += qty
			rows_by_key[key].append((row, batch))

	deviations = []
	for key, batches_used in used.items():
		available = get_fefo_available(*key)
		if not available:
			continue
		expiry_of = {b["batch_no"]: b["expiry_date"] for b in available}
		for row, batch in rows_by_key[key]:
			chosen_expiry = expiry_of.get(batch)
			if not chosen_expiry:
				continue
			earlier = [
				b
				for b in available
				if b["batch_no"] != batch
				and b["expiry_date"]
				and getdate(b["expiry_date"]) < getdate(chosen_expiry)
				and flt(b["qty"]) - flt(batches_used.get(b["batch_no"])) > 0
			]
			if earlier:
				deviations.append({"row": row, "batch": batch, "suggested": earlier[0]})
	return deviations


def check_fefo(doc, method=None):
	"""doc_event (validate) for sales documents."""
	mode = frappe.db.get_single_value("PharmacyOS Settings", "fefo_mode") or "Warn"
	if mode == "Off" or frappe.flags.in_import:
		return
	deviations = find_fefo_deviations(doc)
	if not deviations:
		return

	batch_ids = dict(
		frappe.get_all(
			"Batch",
			filters={
				"name": [
					"in",
					[d["batch"] for d in deviations] + [d["suggested"]["batch_no"] for d in deviations],
				]
			},
			fields=["name", "batch_id"],
			as_list=True,
		)
	)
	lines = [
		_("Row {0}: {1} uses batch {2}, but batch {3} expires earlier ({4}).").format(
			d["row"].idx,
			frappe.bold(d["row"].item_code),
			frappe.bold(batch_ids.get(d["batch"], d["batch"])),
			frappe.bold(batch_ids.get(d["suggested"]["batch_no"], d["suggested"]["batch_no"])),
			frappe.format(d["suggested"]["expiry_date"], {"fieldtype": "Date"}),
		)
		for d in deviations
	]
	message = "<br>".join(lines)
	if mode == "Block":
		frappe.throw(message, title=_("First Expiry, First Out"))
	frappe.msgprint(message, title=_("First Expiry, First Out"), indicator="orange")


def run_sale_validators(doc, method=None):
	"""Extension point for regulated-medicine rules (none shipped). See hooks `pharmacyos_sale_validators`."""
	validators = frappe.get_hooks("pharmacyos_sale_validators")
	if not validators:
		return
	codes = list({r.item_code for r in doc.items})
	items = {
		i.name: i
		for i in frappe.get_all(
			"Item",
			filters={"name": ["in", codes or [""]], "pharma_is_medicine": 1},
			fields=["name", "pharma_dispensing", "pharma_regulatory_class"],
		)
	}
	for row in doc.items:
		if row.item_code in items:
			for path in validators:
				frappe.get_attr(path)(doc, row, items[row.item_code])
