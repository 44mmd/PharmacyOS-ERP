"""Serialised stock decisions: one lock order for every sale, delivery and online reservation.

Two problems share one cause — concurrent transactions deciding about the same item and warehouse
without coordination:

* two cashiers selling the last unit at the same moment: ERPNext's negative-stock check keeps the
  ledger correct, but the transactions lock ledger rows in different orders and one of them dies with
  a database deadlock (HTTP 508) instead of a clear "insufficient stock" message;
* a website order reserving the last unit while a counter sale takes it: both succeed, the ledger
  stays correct, but the customer holds a confirmation that can never be fulfilled.

The fix is to take the item/warehouse `Bin` rows `FOR UPDATE`, in a fixed (sorted) order, at the
very start of saving or submitting any document that moves or reserves stock. Saving a draft counts:
ERPNext already creates the draft's batch bundle entries (and locks those rows) when the draft is
saved, so a draft save racing a submit can otherwise deadlock on batch rows. With every save taking
the Bin locks first, competing transactions wait for each other instead of deadlocking.

MariaDB runs at REPEATABLE READ, so plain reads after the lock may still see the snapshot taken
before the competitor committed. Decisions therefore use *current* (locking) reads of the Bin's
on-hand and reserved quantities (`current_free_qty`); only the expired-batch quantity, which neither
a sale nor a reservation changes, comes from the snapshot.

Under that lock, counter sales (rows not fulfilling a Sales Order) may only use stock that is neither
expired nor reserved by submitted Sales Orders — website orders hold their units until they are
fulfilled or cancelled (PharmacyOS Settings → Protect Website Reservations, on by default).
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

# (doctype, field naming the Sales Order row a line fulfils)
OUTWARD = {
	"Sales Invoice": "so_detail",
	"POS Invoice": "so_detail",
	"Delivery Note": "so_detail",
}
LOCKING_DOCTYPES = (*OUTWARD, "Sales Order")


def lock_bins(pairs) -> None:
	"""Lock the Bin rows of (item_code, warehouse) pairs, always in the same order."""
	for item_code, warehouse in sorted({(i, w) for i, w in pairs if i and w}):
		frappe.db.sql(
			"select name from `tabBin` where item_code=%s and warehouse=%s for update", (item_code, warehouse)
		)


def expired_qty(item_code: str, warehouse: str) -> float:
	"""On-hand quantity of batches already expired (not sellable)."""
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_auto_batch_nos

	today = getdate(nowdate())
	rows = get_auto_batch_nos(
		frappe._dict(
			{
				"item_code": item_code,
				"warehouse": warehouse,
				"for_stock_levels": True,
				"ignore_reserved_stock": True,
				"based_on": "Expiry",
			}
		)
	)
	return sum(
		flt(r.get("qty"))
		for r in rows
		if flt(r.get("qty")) > 0 and r.get("expiry_date") and getdate(r.get("expiry_date")) < today
	)


def current_free_qty(item_code: str, warehouses: list[str], has_batch_no: bool) -> tuple[float, float]:
	"""(sellable, reserved) right now: on-hand minus expired minus reserved, from locking reads.

	Call after `lock_bins` for the same rows. Never negative.
	"""
	free, reserved_total = 0.0, 0.0
	for warehouse in warehouses:
		row = frappe.db.sql(
			"select actual_qty, reserved_qty from `tabBin` where item_code=%s and warehouse=%s for update",
			(item_code, warehouse),
			as_dict=True,
		)
		if not row:
			continue
		actual, reserved = flt(row[0].actual_qty), flt(row[0].reserved_qty)
		expired = expired_qty(item_code, warehouse) if has_batch_no else 0.0
		free += actual - expired - reserved
		reserved_total += reserved
	return max(0.0, flt(free, 3)), flt(reserved_total, 3)


def _moves_stock(doc) -> bool:
	if doc.doctype == "Sales Invoice":
		return bool(cint(doc.get("update_stock")))
	return True


def _stock_rows(doc):
	return [row for row in doc.get("items") or [] if row.get("item_code") and row.get("warehouse")]


def lock_document_stock(doc, method=None):
	"""Lock the document's Bin rows, then (for a return) the original sale, before anything else.

	Runs first in every save, submit and cancel (`document_classes.StockLockFirst`, Frappe's
	`load_doc_before_save`) and again in `before_validate`; taking a lock already held is a no-op.
	That is before ERPNext creates batch bundles (drafts included) or ledger entries, so concurrent
	saves, submissions and cancellations of the same items queue here instead of deadlocking later.
	Every such transaction locks in the same order: Bin rows (sorted), the original sale of a return
	(`returns.lock_original_sale`, which serialises returns against one sale) — or, when an original
	sale itself is cancelled, that sale's row (`returns.guard_original_cancellation`) — then everything
	else.
	"""
	from pharmacyos_erp.pharmacy.returns import guard_original_cancellation, lock_original_sale

	if _moves_stock(doc):
		lock_bins((row.item_code, row.warehouse) for row in _stock_rows(doc))
	lock_original_sale(doc)
	# cancelling an original sale: its own row next (same order as a return), refused while returns exist
	guard_original_cancellation(doc)


def reservations_protected() -> bool:
	"""PharmacyOS Settings → Protect Website Reservations; on unless explicitly switched off."""
	stored = frappe.db.sql(
		"select value from `tabSingles` where doctype='PharmacyOS Settings' and field='protect_online_reservations'"
	)
	return True if not stored else bool(cint(stored[0][0]))


def protect_reservations(doc, method=None):
	"""doc_event (validate) for counter sales: do not sell units reserved for website orders."""
	if doc.docstatus != 1 or doc.get("is_return") or not _moves_stock(doc):
		return
	if doc.doctype not in OUTWARD:
		return
	if not reservations_protected():
		return
	link_field = OUTWARD[doc.doctype]
	needed = defaultdict(float)
	for row in _stock_rows(doc):
		if row.get(link_field):
			continue  # fulfils a Sales Order: uses that order's own reservation
		needed[(row.item_code, row.warehouse)] += abs(flt(row.get("stock_qty") or row.get("qty")))
	if not needed:
		return

	has_batch = dict(
		frappe.get_all(
			"Item",
			filters={"name": ["in", list({k[0] for k in needed})]},
			fields=["name", "has_batch_no"],
			as_list=True,
		)
	)
	problems = []
	for (item_code, warehouse), qty in needed.items():
		free, reserved = current_free_qty(item_code, [warehouse], cint(has_batch.get(item_code)))
		if not reserved:
			continue  # nothing reserved here: ERPNext's own stock checks decide
		if flt(qty, 3) > flt(free, 3):
			problems.append(
				_("{0}: {1} requested, {2} available — {3} reserved for website orders in {4}.").format(
					frappe.bold(item_code), flt(qty, 3), flt(free, 3), reserved, warehouse
				)
			)
	if problems:
		frappe.throw(
			"<br>".join(problems)
			+ "<br>"
			+ _("Fulfil or cancel the website order to release its reserved units."),
			title=_("Reserved for website orders"),
		)
