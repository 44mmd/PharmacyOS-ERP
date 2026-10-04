"""Database indexes that keep concurrent sales and cancellations from deadlocking.

Cancelling any stock document makes ERPNext mark the document's batch bundle rows as cancelled with

	UPDATE `tabSerial and Batch Bundle` … WHERE voucher_type = … AND voucher_no = …
	UPDATE `tabSerial and Batch Entry`  … WHERE voucher_type = … AND voucher_no = …

(`erpnext/stock/serial_batch_bundle.py`). ERPNext ships no index on `voucher_no` for either table
(the bundle has one on `voucher_type` alone, the entry none), so under InnoDB's REPEATABLE READ the
statements scan — and X-lock — every row they examine: all bundle entries of every item, not just the
cancelled document's. A cashier's sale running at the same moment holds the entry row of *its* batch
(bundle creation) and waits for the gap on `tabSales Invoice Item` that the cancellation holds: a
lock cycle, and one of the two ordinary operations dies with HTTP 508 (round-4 finding D-5, observed
on unrelated items and batches).

With a composite `(voucher_type, voucher_no)` index each statement touches only the cancelled
document's own rows, so a cancellation and a sale of a different document never meet on a batch entry
row, and the cycle cannot form. Operations on the *same* item still queue on the item's Bin row
(`stock_guard`), as before.

Applied on install and every migrate (`setup.install.ensure_structure`) and by the round-4 patch;
`frappe.db.add_index` is a no-op when the index exists, and Frappe's schema sync never drops
multi-column indexes.
"""

import frappe

VOUCHER_INDEXES = {
	"Serial and Batch Bundle": ("voucher_type", "voucher_no"),
	"Serial and Batch Entry": ("voucher_type", "voucher_no"),
}


def index_name(fields) -> str:
	return frappe.db.get_index_name(list(fields))


def ensure_voucher_indexes() -> list[str]:
	"""Create the missing indexes; returns the names created (empty when everything was in place)."""
	created = []
	for doctype, fields in VOUCHER_INDEXES.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		table = f"tab{doctype}"
		name = index_name(fields)
		if frappe.db.has_index(table, name):
			continue
		frappe.db.add_index(doctype, list(fields), index_name=name)
		created.append(f"{table}.{name}")
	return created


def voucher_indexes_present() -> dict[str, bool]:
	return {
		doctype: bool(frappe.db.has_index(f"tab{doctype}", index_name(fields)))
		for doctype, fields in VOUCHER_INDEXES.items()
	}
