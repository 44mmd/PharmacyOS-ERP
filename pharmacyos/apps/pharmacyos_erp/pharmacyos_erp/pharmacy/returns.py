"""Return integrity for sales documents (Sales Invoice, POS Invoice, Delivery Note).

ERPNext checks returned quantities only for return rows that carry a link to the original row
(`sales_invoice_item`, `pos_invoice_item`, `dn_detail`). The POS return screen sets that link, but a
return built through the REST API without it is accepted with just a message, so a client could
return more than was sold — an over-refund that also puts phantom stock back on the shelf.

PharmacyOS enforces, for every return that references an original document, regardless of how it was
built:

* every returned item was on the original document;
* cumulative returned quantity per item (all submitted returns, plus this one) never exceeds the
  quantity sold;
* for batch-tracked medicines, the returned batches were sold on the original document and the
  cumulative quantity per batch does not exceed what was sold from that batch;
* the refund rate never exceeds the rate charged.

A medicine return must reference the original sale: a free-standing return would put stock back that
was never sold from this pharmacy.

Credit-note authority (round 3). A return is a refund. Counter staff (anyone without
`CREDIT_NOTE_ROLES`) may only issue the counter return: linked to a sale they can open, bringing the
goods back when the sale took them from stock, within the quantities and rates above. A free-standing
credit note (no original sale, any item) or a money-only credit note against a stock sale needs a
manager's or the accountant's authority (CreditNoteAuthorityError, HTTP 403).

Concurrency — the invariant must hold for simultaneous requests on different server workers:

* **One serialisation point.** Submitting or cancelling a return locks the original document's row
  (`lock_original_sale`) at the start of the transaction, after the stock (Bin) locks and before
  anything else (`stock_guard.lock_document_stock`). Two returns against the same sale therefore run
  one after the other.
* **A current read of what was already returned.** Waiting for the lock is not enough on its own:
  MariaDB runs at REPEATABLE READ, so ordinary reads in the second transaction still see the snapshot
  taken before the first one committed — its return would be invisible. The cumulative returned
  quantities therefore live on the original document itself (`pharma_return_ledger`, one entry per
  submitted return) and are read with a locking read of that same, already locked, row. A locking
  read always returns the latest committed version, never the snapshot.
* **Updated in the same transaction.** The return's entry is written to the ledger on submit and
  removed on cancel, inside the return's own transaction, while the lock is held.

The loser of a race therefore waits for the winner, sees its return, and gets the ordinary
"Return exceeds sale" error (StockOverReturnError, HTTP 417) — never a double refund or a deadlock.
Originals whose ledger was never written (returns submitted before this version) are computed from
their submitted returns, and the upgrade patch fills the ledger for all of them.

Cancelling the original sale takes part in the same serialisation. Invariant: an original sale is
never cancelled while a submitted return against it exists, and a return is never submitted against
a sale that is not (currently) submitted. Frappe's own back-link check on cancel and ERPNext's
"return against must be submitted" check both read the REPEATABLE READ snapshot, so a return and a
cancellation racing each other could both pass (cancelled sale + live refund + phantom stock). Now:

* **Cancelling an original** (`guard_original_cancellation`) locks the sale's row right after its Bin
  rows — the same order as a return — and decides from a locking read of the ledger (the latest
  committed returns, never the snapshot). Any submitted return → SaleHasActiveReturnsError (HTTP 417),
  nothing is cancelled. This runs before Frappe's own locks and checks (`load_doc_before_save`).
* **Submitting a return** re-reads the original's docstatus with a locking read under the same lock;
  a sale cancelled meanwhile → ReturnAgainstCancelledSaleError (HTTP 417), nothing is posted.

Lock order for every participant: Bin rows (sorted), the original sale's row, then the return's own
row and everything else — so the two sides queue on the original's row instead of deadlocking.
"""

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from pharmacyos_erp.pharmacy.fefo import get_row_batches

RETURN_DOCTYPES = ("Sales Invoice", "POS Invoice", "Delivery Note")
LEDGER_FIELD = "pharma_return_ledger"


class ReturnAgainstCancelledSaleError(frappe.ValidationError):
	"""The original sale is no longer submitted (cancelled meanwhile): nothing can be returned against it."""


class SaleHasActiveReturnsError(frappe.LinkExistsError):
	"""The sale has submitted returns: cancel them first, then the sale."""


class CreditNoteAuthorityError(frappe.PermissionError):
	"""A refund beyond the counter return (free-standing, or money only) by a user without authority."""


# roles that may issue credit notes beyond the counter return
CREDIT_NOTE_ROLES = (
	"Pharmacy Owner",
	"Pharmacy Manager",
	"Pharmacy Accountant",
	"Accounts Manager",
	"System Manager",
)


def has_credit_note_authority(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & set(CREDIT_NOTE_ROLES))


def check_credit_note_authority(doc) -> None:
	"""Counter staff: only the linked counter return (see the module doc)."""
	if has_credit_note_authority():
		return
	if not doc.get("return_against"):
		frappe.throw(
			_(
				"A credit note that does not reference the original sale needs a pharmacy manager or the accountant. Use the Return button on the original invoice."
			),
			CreditNoteAuthorityError,
			title=_("Return not linked to a sale"),
		)
	if not frappe.has_permission(doc.doctype, "read", doc=doc.return_against):
		frappe.throw(
			_("You cannot open {0}, so you cannot return against it.").format(
				frappe.bold(doc.return_against)
			),
			CreditNoteAuthorityError,
			title=_("Not permitted"),
		)
	if doc.doctype in ("Sales Invoice", "POS Invoice"):
		took_stock = cint(frappe.db.get_value(doc.doctype, doc.return_against, "update_stock"))
		if took_stock and not cint(doc.get("update_stock")):
			frappe.throw(
				_(
					"{0} took the goods from stock: a counter return brings them back (Update Stock). A refund without the goods needs a pharmacy manager or the accountant."
				).format(frappe.bold(doc.return_against)),
				CreditNoteAuthorityError,
				title=_("Money-only credit note"),
			)


def _over_return_error():
	from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

	return StockOverReturnError


def _row_qty(row) -> float:
	qty = flt(row.get("stock_qty")) or flt(row.get("qty")) * flt(row.get("conversion_factor") or 1)
	return abs(qty)


def _medicines(item_codes) -> dict:
	return {
		i.name: i
		for i in frappe.get_all(
			"Item",
			filters={"name": ["in", list(item_codes) or [""]], "pharma_is_medicine": 1},
			fields=["name", "has_batch_no"],
		)
	}


def _totals(doc):
	"""(qty per item, qty per (item, batch), max rate per item) of a document's rows."""
	qty, batch_qty, rate = defaultdict(float), defaultdict(float), defaultdict(float)
	for row in doc.get("items") or []:
		if not row.item_code:
			continue
		qty[row.item_code] += _row_qty(row)
		rate[row.item_code] = max(rate[row.item_code], flt(row.get("rate")))
		for batch, batch_qty_used in get_row_batches(doc, row).items():
			batch_qty[(row.item_code, batch)] += abs(flt(batch_qty_used))
	return qty, batch_qty, rate


def is_tracked_return(doc) -> bool:
	return bool(
		doc.doctype in RETURN_DOCTYPES
		and doc.get("is_return")
		and doc.get("return_against")
		and not doc.get("is_consolidated")
	)


# ------------------------------------------------------------------ serialisation and the ledger


def lock_original_sale(doc) -> None:
	"""Lock the original sale's row while a return against it is submitted or cancelled.

	Called by `stock_guard.lock_document_stock`, i.e. at the very start of the save (Frappe's
	`load_doc_before_save`) and again in `before_validate`; re-locking a row already held is a no-op.
	"""
	if doc.docstatus in (1, 2) and is_tracked_return(doc):
		frappe.db.sql(f"select name from `tab{doc.doctype}` where name=%s for update", doc.return_against)


def ensure_original_submitted(doc) -> None:
	"""A return being submitted: the original must be submitted *now* (locking read, not the snapshot)."""
	row = frappe.db.sql(
		f"select docstatus from `tab{doc.doctype}` where name=%s for update", doc.return_against
	)
	if not row or cint(row[0][0]) != 1:
		frappe.throw(
			_("{0} is cancelled, so nothing can be returned against it.").format(
				frappe.bold(doc.return_against)
			),
			ReturnAgainstCancelledSaleError,
			title=_("Original sale cancelled"),
		)


def active_returns(doctype: str, original: str) -> list[str]:
	"""Submitted returns against `original`, from current (locking) reads. Call with its row locked."""
	ledger = _read_ledger(doctype, original, current=True)
	if ledger is not None:
		return sorted(ledger["returns"])
	# never written (no return since the ledger exists, or an original from before it): the returns'
	# own rows, read with a locking read so a return committed after this transaction's snapshot counts
	return [
		r[0]
		for r in frappe.db.sql(
			f"""select name from `tab{doctype}`
			where return_against=%s and is_return=1 and docstatus=1 for update""",
			original,
		)
	]


def guard_original_cancellation(doc) -> None:
	"""An original sale being cancelled: lock its row and refuse while a submitted return exists.

	Called by `stock_guard.lock_document_stock` (after the Bin locks, before Frappe locks and reloads
	the document). Only a submitted, non-return document moving to cancelled is concerned.
	"""
	if doc.doctype not in RETURN_DOCTYPES or doc.get("is_return") or doc.docstatus != 2 or doc.is_new():
		return
	row = frappe.db.sql(f"select docstatus from `tab{doc.doctype}` where name=%s for update", doc.name)
	if not row or cint(row[0][0]) != 1:
		return  # not a submitted sale being cancelled (e.g. a draft discarded)
	returns = active_returns(doc.doctype, doc.name)
	if returns:
		frappe.throw(
			_(
				"{0} cannot be cancelled while returns against it are submitted: {1}. Cancel the returns first."
			).format(frappe.bold(doc.name), ", ".join(returns)),
			SaleHasActiveReturnsError,
			title=_("Sale has returns"),
		)


def _contribution(doc) -> dict:
	"""What one return document returns: {"items": {item: qty}, "batches": {item: {batch: qty}}}."""
	qty, batch_qty, _rate = _totals(doc)
	batches = defaultdict(dict)
	for (item_code, batch), value in batch_qty.items():
		batches[item_code][batch] = flt(value, 9)
	return {"items": {k: flt(v, 9) for k, v in qty.items()}, "batches": dict(batches)}


def _read_ledger(doctype: str, original: str, current: bool) -> dict | None:
	"""The stored ledger. `current=True` is a locking read: the latest committed value, not the snapshot."""
	rows = frappe.db.sql(
		f"select `{LEDGER_FIELD}` from `tab{doctype}` where name=%s{' for update' if current else ''}",
		original,
	)
	raw = rows[0][0] if rows else None
	if not raw:
		return None
	ledger = json.loads(raw)
	ledger.setdefault("returns", {})
	return ledger


def _write_ledger(doctype: str, original: str, ledger: dict) -> None:
	# a direct column update: the original stays unmodified (no new `modified`, no version entry)
	frappe.db.sql(
		f"update `tab{doctype}` set `{LEDGER_FIELD}`=%s where name=%s",
		(json.dumps(ledger, sort_keys=True, separators=(",", ":")), original),
	)


def compute_ledger(doctype: str, original: str) -> dict:
	"""Ledger rebuilt from the submitted return documents (upgrade patch, reconciliation, fallback)."""
	names = frappe.get_all(
		doctype,
		filters={"return_against": original, "is_return": 1, "docstatus": 1},
		pluck="name",
		order_by="creation asc",
	)
	return {"returns": {name: _contribution(frappe.get_doc(doctype, name)) for name in names}}


def rebuild_ledger(doctype: str, original: str) -> dict:
	ledger = compute_ledger(doctype, original)
	_write_ledger(doctype, original, ledger)
	return ledger


def _ledger(doctype: str, original: str, current: bool) -> dict:
	ledger = _read_ledger(doctype, original, current)
	if ledger is None:
		# never written: no return was submitted against it since PharmacyOS keeps the ledger (any
		# such return writes it before committing, and the locking read above would see it), so the
		# submitted documents are the complete history
		ledger = compute_ledger(doctype, original)
	return ledger


def returned_so_far(doctype: str, original: str, exclude: str | None = None, current: bool = True):
	"""(qty per item, qty per (item, batch)) returned by submitted returns, excluding `exclude`."""
	ledger = _ledger(doctype, original, current)
	returned, returned_batches = defaultdict(float), defaultdict(float)
	for name, entry in ledger["returns"].items():
		if name == exclude:
			continue
		for item_code, qty in (entry.get("items") or {}).items():
			returned[item_code] += flt(qty)
		for item_code, batches in (entry.get("batches") or {}).items():
			for batch, qty in batches.items():
				returned_batches[(item_code, batch)] += flt(qty)
	return returned, returned_batches


def record_return(doc, method=None):
	"""doc_event (on_submit / on_cancel): add or remove this return's entry in the original's ledger."""
	if not is_tracked_return(doc):
		return
	lock_original_sale(doc)  # already held since the start of the transaction; kept for safety
	ledger = _ledger(doc.doctype, doc.return_against, current=True)
	if doc.docstatus == 1:
		ledger["returns"][doc.name] = _contribution(doc)
	else:
		ledger["returns"].pop(doc.name, None)
	_write_ledger(doc.doctype, doc.return_against, ledger)


# ------------------------------------------------------------------ validation


def validate_return(doc, method=None):
	"""doc_event (validate) for Sales Invoice, POS Invoice and Delivery Note."""
	if not doc.get("is_return") or doc.get("is_consolidated"):
		return
	items = {row.item_code for row in doc.get("items") or [] if row.item_code}
	medicines = _medicines(items)
	check_credit_note_authority(doc)

	if not doc.get("return_against"):
		if medicines:
			frappe.throw(
				_(
					"A medicine return must reference the original sale (Return Against). Use the Return button on the original invoice."
				),
				title=_("Return not linked to a sale"),
			)
		return

	submitting = doc.docstatus == 1
	if submitting:
		# normally already held since the start of the transaction (stock_guard.lock_document_stock)
		lock_original_sale(doc)
		# ERPNext checked the original's docstatus in this transaction's snapshot; a cancellation that
		# committed while we waited for the lock is only visible to a locking read
		ensure_original_submitted(doc)

	original = frappe.get_doc(doc.doctype, doc.return_against)
	sold, sold_batches, sold_rate = _totals(original)
	# on submit: a locking read of the ledger on the locked original — includes every return
	# committed by a concurrent request; drafts get the same check as early, advisory feedback
	returned, returned_batches = returned_so_far(
		doc.doctype, doc.return_against, exclude=doc.name, current=submitting
	)

	this_qty, this_batches, this_rate = _totals(doc)
	error = _over_return_error()
	precision = frappe.get_precision(f"{doc.doctype} Item", "stock_qty") or 3

	for item_code, qty in this_qty.items():
		if item_code not in sold:
			frappe.throw(
				_("Item {0} was not sold on {1}, so it cannot be returned against it.").format(
					frappe.bold(item_code), frappe.bold(doc.return_against)
				),
				error,
				title=_("Return exceeds sale"),
			)
		remaining = flt(sold[item_code] - returned[item_code], precision)
		if flt(qty, precision) > remaining:
			frappe.throw(
				_("{0}: {1} sold on {2}, {3} already returned; at most {4} can be returned.").format(
					frappe.bold(item_code),
					flt(sold[item_code], precision),
					frappe.bold(doc.return_against),
					flt(returned[item_code], precision),
					max(remaining, 0),
				),
				error,
				title=_("Return exceeds sale"),
			)
		if flt(this_rate[item_code], 2) > flt(sold_rate[item_code], 2):
			frappe.throw(
				_("{0}: the refund rate {1} is higher than the rate charged on {2} ({3}).").format(
					frappe.bold(item_code),
					this_rate[item_code],
					frappe.bold(doc.return_against),
					sold_rate[item_code],
				),
				error,
				title=_("Return exceeds sale"),
			)

	for (item_code, batch), qty in this_batches.items():
		item = medicines.get(item_code)
		if not item or not item.has_batch_no:
			continue
		sold_qty = sold_batches.get((item_code, batch), 0.0)
		batch_id = frappe.db.get_value("Batch", batch, "batch_id") or batch
		if not sold_qty:
			frappe.throw(
				_("{0}: batch {1} was not sold on {2}. Return the batch that was sold.").format(
					frappe.bold(item_code), frappe.bold(batch_id), frappe.bold(doc.return_against)
				),
				error,
				title=_("Return exceeds sale"),
			)
		remaining = flt(sold_qty - returned_batches.get((item_code, batch), 0.0), precision)
		if flt(qty, precision) > remaining:
			frappe.throw(
				_("{0}: at most {1} of batch {2} can still be returned against {3}.").format(
					frappe.bold(item_code),
					max(remaining, 0),
					frappe.bold(batch_id),
					frappe.bold(doc.return_against),
				),
				error,
				title=_("Return exceeds sale"),
			)
