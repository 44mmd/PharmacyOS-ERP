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

Concurrent returns against the same original are serialised by locking the original document's row
when a return is submitted, so two simultaneous returns cannot each see the "remaining" quantity.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from pharmacyos_erp.pharmacy.fefo import get_row_batches


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


def validate_return(doc, method=None):
	"""doc_event (validate) for Sales Invoice, POS Invoice and Delivery Note."""
	if not doc.get("is_return") or doc.get("is_consolidated"):
		return
	items = {row.item_code for row in doc.get("items") or [] if row.item_code}
	medicines = _medicines(items)

	if not doc.get("return_against"):
		if medicines:
			frappe.throw(
				_(
					"A medicine return must reference the original sale (Return Against). Use the Return button on the original invoice."
				),
				title=_("Return not linked to a sale"),
			)
		return

	if doc.docstatus == 1:
		# serialise concurrent returns against the same sale: the second waits for the first to commit
		frappe.db.get_value(doc.doctype, doc.return_against, "name", for_update=True)

	original = frappe.get_doc(doc.doctype, doc.return_against)
	sold, sold_batches, sold_rate = _totals(original)

	other_returns = frappe.get_all(
		doc.doctype,
		filters={
			"return_against": doc.return_against,
			"is_return": 1,
			"docstatus": 1,
			"name": ["!=", doc.name or ""],
		},
		pluck="name",
	)
	returned, returned_batches = defaultdict(float), defaultdict(float)
	for name in other_returns:
		qty, batch_qty, _rate = _totals(frappe.get_doc(doc.doctype, name))
		for key, value in qty.items():
			returned[key] += value
		for key, value in batch_qty.items():
			returned_batches[key] += value

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
					frappe.bold(item_code), max(remaining, 0), frappe.bold(batch_id), frappe.bold(doc.return_against)
				),
				error,
				title=_("Return exceeds sale"),
			)
