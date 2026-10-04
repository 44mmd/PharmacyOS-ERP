"""Return integrity for sales documents (Sales Invoice, POS Invoice, Delivery Note).

ERPNext checks returned quantities only for return rows that carry a link to the original row
(`sales_invoice_item`, `pos_invoice_item`, `dn_detail`). The POS return screen sets that link, but a
return built through the REST API without it is accepted with just a message, so a client could
return more than was sold — an over-refund that also puts phantom stock back on the shelf.

PharmacyOS enforces, for every return that references an original document, regardless of how it was
built:

* **the reference is the original sale** — a submitted document that is not itself a return. A return
  referencing another return (ReturnAgainstReturnError) is refused: it would open a fresh allowance
  equal to the earlier return's quantity, so a chain of returns could restore more stock and refund more
  money than the sale ever involved (round-4 finding). Every quantity and amount below is measured
  against that one **root sale**, and every return related to it — directly, or through a chain written
  before this rule existed — counts against the same allowance;
* every returned item was on the root sale;
* cumulative returned quantity per item (all submitted returns, plus this one) never exceeds the
  quantity sold, compared at the stock quantity precision of the document, on the cumulative sum (so
  no sequence of small fractional returns can exceed the sale through rounding);
* for batch-tracked medicines, the returned batches were sold on the root sale and the cumulative
  quantity per batch does not exceed what was sold from that batch;
* the refund rate per stock unit never exceeds the rate charged, in any UOM;
* the economic value refunded never exceeds what the root sale charged for the goods returned: per
  item, the cumulative refunded net amount stays within the returned quantity's share of the item's
  net amount on the sale (row and document discounts included), and the cumulative refund total
  (taxes included) stays within that share scaled by the sale's own gross/net ratio
  (RefundExceedsSaleError). A partial return is worth its portion, never the whole sale; a tax or a
  rate the sale never charged is not refundable. Rounding of distributed discounts is tolerated up
  to one currency unit per unit of goods returned, never more.

A medicine return must reference the original sale: a free-standing return would put stock back that
was never sold from this pharmacy.

Credit-note authority (round 3). A return is a refund. Counter staff (anyone without
`CREDIT_NOTE_ROLES`) may only issue the counter return: linked to a sale they can open, bringing the
goods back when the sale took them from stock, within the quantities and rates above. A free-standing
credit note (no original sale, any item) or a money-only credit note against a stock sale needs a
manager's or the accountant's authority (CreditNoteAuthorityError, HTTP 403).

ERPNext's own return relationships are preserved: the consolidated credit notes that POS closing
creates (`is_consolidated`) reference the consolidated Sales Invoice — an original, never a return —
and are written by ERPNext from already validated POS returns, so they are not re-validated here, but
they are recorded in the consolidated invoice's ledger so that no further return against it can refund
the same goods again.

Concurrency — the invariant must hold for simultaneous requests on different server workers:

* **One serialisation point.** Submitting or cancelling a return locks the root sale's row
  (`lock_original_sale`) at the start of the transaction, after the stock (Bin) locks and before
  anything else (`stock_guard.lock_document_stock`). Two returns against the same sale therefore run
  one after the other.
* **A current read of what was already returned.** Waiting for the lock is not enough on its own:
  MariaDB runs at REPEATABLE READ, so ordinary reads in the second transaction still see the snapshot
  taken before the first one committed — its return would be invisible. The cumulative returned
  quantities and amounts therefore live on the root sale itself (`pharma_return_ledger`, one entry per
  submitted return) and are read with a locking read of that same, already locked, row. A locking
  read always returns the latest committed version, never the snapshot.
* **Updated in the same transaction.** The return's entry is written to the ledger on submit and
  removed on cancel, inside the return's own transaction, while the lock is held.

The loser of a race therefore waits for the winner, sees its return, and gets the ordinary
"Return exceeds sale" error (StockOverReturnError, HTTP 417) — never a double refund or a deadlock.
Originals whose ledger was never written (returns submitted before this version) are computed from
their submitted returns, and the upgrade patches fill the ledger for all of them.

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

Lock order for every participant: Bin rows (sorted), the root sale's row, then the return's own
row and everything else — so the two sides queue on the root's row instead of deadlocking.
"""

import json
import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from pharmacyos_erp.pharmacy.fefo import get_row_batches

RETURN_DOCTYPES = ("Sales Invoice", "POS Invoice", "Delivery Note")
LEDGER_FIELD = "pharma_return_ledger"


class ReturnAgainstCancelledSaleError(frappe.ValidationError):
	"""The referenced sale is not a submitted document (draft, or cancelled meanwhile): nothing can be
	returned against it."""


class ReturnAgainstReturnError(frappe.ValidationError):
	"""The referenced document is itself a return: a return must reference the original sale."""


class SaleHasActiveReturnsError(frappe.LinkExistsError):
	"""The sale has submitted returns: cancel them first, then the sale."""


class CreditNoteAuthorityError(frappe.PermissionError):
	"""A refund beyond the counter return (free-standing, or money only) by a user without authority."""


def _over_return_error():
	from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

	return StockOverReturnError


class RefundExceedsSaleError(frappe.ValidationError):
	"""The money refunded (per item, or in total with taxes) would exceed what the root sale charged."""


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


# ------------------------------------------------------------------ document measures


def _conversion_factor(row) -> float:
	return flt(row.get("conversion_factor")) or 1.0


def _row_qty(row) -> float:
	"""Quantity in stock units (the UOM the ledger and the batches count in)."""
	qty = flt(row.get("stock_qty")) or flt(row.get("qty")) * _conversion_factor(row)
	return abs(qty)


def _row_rate(row) -> float:
	"""Rate per stock unit in company currency, so returns in another UOM compare with the sale."""
	rate = row.get("base_rate")
	if rate is None:
		rate = row.get("rate")
	return abs(flt(rate)) / _conversion_factor(row)


def _row_amount(row) -> float:
	"""Net amount of the row (after any discount) in company currency."""
	for field in ("base_net_amount", "net_amount", "base_amount", "amount"):
		if row.get(field) is not None:
			return abs(flt(row.get(field)))
	return 0.0


def _doc_total(doc) -> float:
	"""What the document charges or refunds, taxes included, before ERPNext's whole-unit rounding.

	The unrounded total is compared on both sides: the rounding adjustment of each document (at most
	half a currency unit, posted to the round-off account by ERPNext) is the only play left to rounding.
	"""
	for field in ("base_grand_total", "grand_total"):
		if flt(doc.get(field)):
			return abs(flt(doc.get(field)))
	for field in ("base_rounded_total", "rounded_total"):
		if flt(doc.get(field)):
			return abs(flt(doc.get(field)))
	return 0.0


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
	"""Per item: quantity (stock units), quantity per (item, batch), highest rate per stock unit and
	net amount; plus the document total."""
	qty, batch_qty, rate, amount = (
		defaultdict(float),
		defaultdict(float),
		defaultdict(float),
		defaultdict(float),
	)
	for row in doc.get("items") or []:
		if not row.item_code:
			continue
		qty[row.item_code] += _row_qty(row)
		rate[row.item_code] = max(rate[row.item_code], _row_rate(row))
		amount[row.item_code] += _row_amount(row)
		for batch, batch_qty_used in get_row_batches(doc, row).items():
			batch_qty[(row.item_code, batch)] += abs(flt(batch_qty_used))
	return frappe._dict(qty=qty, batches=batch_qty, rate=rate, amount=amount, total=_doc_total(doc))


def qty_precision(doctype: str) -> int:
	"""Stock quantity precision of the document's item rows — the precision the stock ledger posts at."""
	return cint(frappe.get_precision(f"{doctype} Item", "stock_qty")) or 3


def amount_precision(doctype: str) -> int:
	return cint(frappe.get_precision(doctype, "base_grand_total")) or 2


def is_tracked_return(doc) -> bool:
	"""A return that counts against a sale's allowance (ERPNext's consolidated credit notes included)."""
	return bool(doc.doctype in RETURN_DOCTYPES and doc.get("is_return") and doc.get("return_against"))


# ------------------------------------------------------------------ the root sale


def root_sale(doctype: str, name: str) -> tuple[str, list[str]]:
	"""(root sale, chain) — follow `return_against` until a document that is not a return.

	For a return written under the current rule the root is its `return_against`; `chain` lists the
	intermediate returns of a historical return-to-return relationship (oldest reference last).
	"""
	chain, seen, current = [], set(), name
	while current and current not in seen:
		seen.add(current)
		row = frappe.db.get_value(doctype, current, ["is_return", "return_against"], as_dict=True)
		if not row or not cint(row.is_return) or not row.return_against:
			return current, chain
		chain.append(current)
		current = row.return_against
	return current, chain  # a cycle (impossible for submitted documents): stop at the repeated name


def _root_of(doc) -> str:
	"""The root sale of a return document (cached on the document for the transaction)."""
	cached = doc.flags.get("pharmacyos_root_sale")
	if cached:
		return cached
	root, _chain = root_sale(doc.doctype, doc.return_against)
	doc.flags.pharmacyos_root_sale = root
	return root


# ------------------------------------------------------------------ serialisation and the ledger


def lock_original_sale(doc) -> None:
	"""Lock the root sale's row while a return against it is submitted or cancelled.

	Called by `stock_guard.lock_document_stock`, i.e. at the very start of the save (Frappe's
	`load_doc_before_save`) and again in `before_validate`; re-locking a row already held is a no-op.
	"""
	if doc.docstatus in (1, 2) and is_tracked_return(doc):
		frappe.db.sql(f"select name from `tab{doc.doctype}` where name=%s for update", _root_of(doc))


def ensure_original_submitted(doc) -> None:
	"""A return being submitted: the original must be submitted *now* (locking read, not the snapshot)."""
	row = frappe.db.sql(
		f"select docstatus from `tab{doc.doctype}` where name=%s for update", doc.return_against
	)
	if not row or cint(row[0][0]) != 1:
		_throw_not_submitted(doc.return_against, cint(row[0][0]) if row else None)


def _throw_not_submitted(name: str, docstatus: int | None) -> None:
	if docstatus == 2:
		message = _("{0} is cancelled, so nothing can be returned against it.")
	else:
		message = _("{0} is not a submitted sale, so nothing can be returned against it.")
	frappe.throw(
		message.format(frappe.bold(name)),
		ReturnAgainstCancelledSaleError,
		title=_("Original sale not submitted"),
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
	"""What one return document returns: quantities per item and batch, net amounts, the total."""
	totals = _totals(doc)
	batches = defaultdict(dict)
	for (item_code, batch), value in totals.batches.items():
		batches[item_code][batch] = flt(value, 9)
	return {
		"items": {k: flt(v, 9) for k, v in totals.qty.items()},
		"batches": dict(batches),
		"amounts": {k: flt(v, 9) for k, v in totals.amount.items()},
		"total": flt(totals.total, 9),
	}


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


def related_returns(doctype: str, original: str) -> list[str]:
	"""Every submitted return whose root sale is `original`: direct returns and, for data written
	before the no-chain rule, returns referencing those returns (any depth), oldest first."""
	found, frontier, seen = [], [original], {original}
	while frontier:
		rows = frappe.get_all(
			doctype,
			filters={"return_against": ["in", frontier], "is_return": 1, "docstatus": 1},
			fields=["name", "creation"],
		)
		frontier = [r.name for r in rows if r.name not in seen]
		seen.update(frontier)
		found += rows
	return [r.name for r in sorted(found, key=lambda r: (r.creation, r.name))]


def compute_ledger(doctype: str, original: str) -> dict:
	"""Ledger rebuilt from the submitted return documents (upgrade patch, reconciliation, fallback)."""
	return {
		"returns": {
			name: _contribution(frappe.get_doc(doctype, name)) for name in related_returns(doctype, original)
		}
	}


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
	elif any("amounts" not in entry for entry in ledger["returns"].values()):
		# written by a version that tracked quantities only: complete it from the documents
		ledger = rebuild_ledger(doctype, original) if current else compute_ledger(doctype, original)
	return ledger


def returned_so_far(doctype: str, original: str, exclude: str | None = None, current: bool = True):
	"""Cumulative returns by submitted returns against the root sale, excluding `exclude`:
	(qty per item, qty per (item, batch), net amount per item, refund total)."""
	ledger = _ledger(doctype, original, current)
	returned, returned_batches, refunded = defaultdict(float), defaultdict(float), defaultdict(float)
	total = 0.0
	for name, entry in ledger["returns"].items():
		if name == exclude:
			continue
		for item_code, qty in (entry.get("items") or {}).items():
			returned[item_code] += flt(qty)
		for item_code, batches in (entry.get("batches") or {}).items():
			for batch, qty in batches.items():
				returned_batches[(item_code, batch)] += flt(qty)
		for item_code, amount in (entry.get("amounts") or {}).items():
			refunded[item_code] += flt(amount)
		total += flt(entry.get("total"))
	return frappe._dict(qty=returned, batches=returned_batches, amount=refunded, total=total)


def record_return(doc, method=None):
	"""doc_event (on_submit / on_cancel): add or remove this return's entry in the root sale's ledger."""
	if not is_tracked_return(doc):
		return
	root = _root_of(doc)
	lock_original_sale(doc)  # already held since the start of the transaction; kept for safety
	ledger = _ledger(doc.doctype, root, current=True)
	if doc.docstatus == 1:
		ledger["returns"][doc.name] = _contribution(doc)
	else:
		ledger["returns"].pop(doc.name, None)
	_write_ledger(doc.doctype, root, ledger)


# ------------------------------------------------------------------ validation


def _check_reference(doc) -> None:
	"""The reference must be the original sale: a submitted document that is not a return."""
	ref = frappe.db.get_value(
		doc.doctype, doc.return_against, ["is_return", "docstatus", "return_against"], as_dict=True
	)
	if not ref:
		frappe.throw(
			_("{0} does not exist, so nothing can be returned against it.").format(
				frappe.bold(doc.return_against)
			),
			ReturnAgainstCancelledSaleError,
			title=_("Original sale not found"),
		)
	if cint(ref.is_return):
		root, _chain = root_sale(doc.doctype, doc.return_against)
		frappe.throw(
			_(
				"{0} is itself a return. A return must reference the original sale ({1}), never another return."
			).format(frappe.bold(doc.return_against), frappe.bold(root)),
			ReturnAgainstReturnError,
			title=_("Return against a return"),
		)
	if cint(ref.docstatus) != 1:
		_throw_not_submitted(doc.return_against, cint(ref.docstatus))


def _rounding_tolerance(unit: float, qty: float) -> float:
	"""One currency unit per unit of goods returned (distributed discounts round per row)."""
	return unit * max(1, math.ceil(flt(qty) - 1e-9))


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

	_check_reference(doc)
	submitting = doc.docstatus == 1
	if submitting:
		# normally already held since the start of the transaction (stock_guard.lock_document_stock)
		lock_original_sale(doc)
		# ERPNext checked the original's docstatus in this transaction's snapshot; a cancellation that
		# committed while we waited for the lock is only visible to a locking read
		ensure_original_submitted(doc)

	original = frappe.get_doc(doc.doctype, doc.return_against)
	sold = _totals(original)
	# on submit: a locking read of the ledger on the locked original — includes every return
	# committed by a concurrent request; drafts get the same check as early, advisory feedback
	returned = returned_so_far(doc.doctype, doc.return_against, exclude=doc.name, current=submitting)

	this = _totals(doc)
	error = _over_return_error()
	precision = qty_precision(doc.doctype)
	currency_precision = amount_precision(doc.doctype)
	unit = 10**-currency_precision

	for item_code, qty in this.qty.items():
		if item_code not in sold.qty:
			frappe.throw(
				_("Item {0} was not sold on {1}, so it cannot be returned against it.").format(
					frappe.bold(item_code), frappe.bold(doc.return_against)
				),
				error,
				title=_("Return exceeds sale"),
			)
		if flt(qty, precision) <= 0:
			frappe.throw(
				_("{0}: the returned quantity {1} is below the stock precision ({2} decimals).").format(
					frappe.bold(item_code), qty, precision
				),
				error,
				title=_("Return exceeds sale"),
			)
		already = returned.qty[item_code]
		remaining = flt(sold.qty[item_code] - already, precision)
		if flt(already + qty, precision) > flt(sold.qty[item_code], precision):
			frappe.throw(
				_("{0}: {1} sold on {2}, {3} already returned; at most {4} can be returned.").format(
					frappe.bold(item_code),
					flt(sold.qty[item_code], precision),
					frappe.bold(doc.return_against),
					flt(already, precision),
					max(remaining, 0),
				),
				error,
				title=_("Return exceeds sale"),
			)
		if flt(this.rate[item_code], currency_precision) > flt(sold.rate[item_code], currency_precision):
			frappe.throw(
				_("{0}: the refund rate {1} is higher than the rate charged on {2} ({3}).").format(
					frappe.bold(item_code),
					flt(this.rate[item_code], currency_precision),
					frappe.bold(doc.return_against),
					flt(sold.rate[item_code], currency_precision),
				),
				error,
				title=_("Return exceeds sale"),
			)
		# money: the returned portion is worth its share of what the sale charged for the item (net
		# of row and document discounts), never what the return document says it is worth
		unit_value = sold.amount[item_code] / sold.qty[item_code] if sold.qty[item_code] else 0.0
		refunded = returned.amount[item_code] + this.amount[item_code]
		entitled = unit_value * (already + qty)
		allowed = entitled + _rounding_tolerance(unit, already + qty)
		if flt(refunded, currency_precision) > flt(allowed, currency_precision):
			frappe.throw(
				_("{0}: {1} charged on {2}, {3} already refunded; at most {4} can be refunded.").format(
					frappe.bold(item_code),
					flt(sold.amount[item_code], currency_precision),
					frappe.bold(doc.return_against),
					flt(returned.amount[item_code], currency_precision),
					max(flt(entitled - returned.amount[item_code], currency_precision), 0),
				),
				RefundExceedsSaleError,
				title=_("Refund exceeds sale"),
			)

	for (item_code, batch), qty in this.batches.items():
		item = medicines.get(item_code)
		if not item or not item.has_batch_no:
			continue
		sold_qty = sold.batches.get((item_code, batch), 0.0)
		batch_id = frappe.db.get_value("Batch", batch, "batch_id") or batch
		if not sold_qty:
			frappe.throw(
				_("{0}: batch {1} was not sold on {2}. Return the batch that was sold.").format(
					frappe.bold(item_code), frappe.bold(batch_id), frappe.bold(doc.return_against)
				),
				error,
				title=_("Return exceeds sale"),
			)
		already = returned.batches.get((item_code, batch), 0.0)
		if flt(already + qty, precision) > flt(sold_qty, precision):
			frappe.throw(
				_("{0}: at most {1} of batch {2} can still be returned against {3}.").format(
					frappe.bold(item_code),
					max(flt(sold_qty - already, precision), 0),
					frappe.bold(batch_id),
					frappe.bold(doc.return_against),
				),
				error,
				title=_("Return exceeds sale"),
			)

	# the total (taxes included): the returned portions' net value, scaled by the sale's own
	# gross/net ratio — taxes follow the goods; a tax row the sale never charged is not refundable
	refund_total = returned.total + this.total
	net_sold = sum(sold.amount.values())
	gross_ratio = sold.total / net_sold if net_sold else 1.0
	entitled_net = sum(
		(sold.amount[item] / sold.qty[item] if sold.qty[item] else 0.0)
		* (returned.qty[item] + this.qty.get(item, 0.0))
		for item in set(returned.qty) | set(this.qty)
		if item in sold.qty
	)
	returned_units = sum(returned.qty.values()) + sum(this.qty.values())
	allowed_total = entitled_net * gross_ratio + _rounding_tolerance(unit, returned_units)
	if flt(refund_total, currency_precision) > flt(allowed_total, currency_precision):
		frappe.throw(
			_("{0} charged {1} in total, {2} already refunded; this return would refund {3}.").format(
				frappe.bold(doc.return_against),
				flt(sold.total, currency_precision),
				flt(returned.total, currency_precision),
				flt(this.total, currency_precision),
			),
			RefundExceedsSaleError,
			title=_("Refund exceeds sale"),
		)
