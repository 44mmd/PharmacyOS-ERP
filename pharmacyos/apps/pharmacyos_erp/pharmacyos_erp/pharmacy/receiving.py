"""Receiving rules for medicines (Purchase Receipt, and Purchase Invoice with Update Stock).

Only rows whose item is a batch-tracked medicine are checked; everything else is ERPNext as usual.
Rules (PharmacyOS Settings → Batches & Expiry):

* a batch must be given (the supplier's printed batch number) — ERPNext also requires this;
* the batch must have an expiry date (`require_expiry_on_receipt`);
* a batch already expired at the posting date is rejected (`block_expired_receipt`);
* remaining shelf life below `min_shelf_life_on_receipt` days warns or blocks
  (`short_shelf_life_action`).

Validation only — quantities, valuation, accounting and batch creation remain ERPNext's.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, getdate

from pharmacyos_erp.pharmacy.fefo import get_row_batches


def validate_receipt(doc, method=None):
	if doc.get("is_return"):
		return
	if doc.doctype == "Purchase Invoice" and not doc.get("update_stock"):
		return

	settings = frappe.get_cached_doc("PharmacyOS Settings")
	codes = list({r.item_code for r in doc.items if r.item_code})
	medicines = set(
		frappe.get_all(
			"Item",
			filters={"name": ["in", codes or [""]], "pharma_is_medicine": 1, "has_batch_no": 1},
			pluck="name",
		)
	)
	if not medicines:
		return

	posting_date = getdate(doc.get("posting_date") or frappe.utils.nowdate())
	errors, warnings = [], []
	row_batches = {}
	for row in doc.items:
		if row.item_code not in medicines:
			continue
		# rows without a batch are left to ERPNext, which requires one for batch-tracked items
		row_batches[row.idx] = get_row_batches(doc, row)

	all_batches = list({b for bs in row_batches.values() for b in bs})
	info = {
		b.name: b
		for b in frappe.get_all(
			"Batch",
			filters={"name": ["in", all_batches or [""]]},
			fields=["name", "batch_id", "expiry_date", "item"],
		)
	}
	min_days = cint(settings.min_shelf_life_on_receipt)
	for row in doc.items:
		for batch in row_batches.get(row.idx, {}):
			b = info.get(batch)
			if not b:
				continue
			label = _("Row {0}: {1}, batch {2}").format(
				row.idx, frappe.bold(row.item_code), frappe.bold(b.batch_id)
			)
			if not b.expiry_date:
				if settings.require_expiry_on_receipt:
					errors.append(
						_("{0} has no expiry date. Enter the expiry date printed on the pack.").format(label)
					)
				continue
			days = date_diff(getdate(b.expiry_date), posting_date)
			if days < 0:
				if settings.block_expired_receipt:
					errors.append(
						_("{0} expired on {1}. Expired medicines cannot be received.").format(
							label, frappe.format(b.expiry_date, {"fieldtype": "Date"})
						)
					)
			elif min_days and days < min_days:
				msg = _("{0} has only {1} days of shelf life left (minimum {2}).").format(
					label, days, min_days
				)
				(errors if settings.short_shelf_life_action == "Block" else warnings).append(msg)

	if errors:
		frappe.throw("<br>".join(errors), title=_("Check batch & expiry"))
	if warnings:
		frappe.msgprint("<br>".join(warnings), title=_("Short shelf life"), indicator="orange")


@frappe.whitelist(methods=["POST"])
def create_receiving_batch(
	item_code: str,
	batch_id: str,
	expiry_date: str,
	manufacturing_date: str | None = None,
	supplier: str | None = None,
) -> dict:
	"""Create (or reuse) the supplier's batch for receiving. Returns {name, batch_id, expiry_date}.

	Uses the normal Batch insert, so the user's Batch create permission applies.
	"""
	batch_id = (batch_id or "").strip()
	if not batch_id:
		frappe.throw(_("Enter the batch number printed on the pack."))
	if not expiry_date:
		frappe.throw(_("Enter the expiry date printed on the pack."))
	if manufacturing_date and getdate(manufacturing_date) > getdate(expiry_date):
		frappe.throw(_("Manufacturing date cannot be after the expiry date."))

	existing = frappe.db.get_value(
		"Batch", {"item": item_code, "batch_id": batch_id}, ["name", "expiry_date"], as_dict=True
	)
	if existing:
		if existing.expiry_date and getdate(existing.expiry_date) != getdate(expiry_date):
			frappe.throw(
				_("Batch {0} already exists for this medicine with expiry {1}. Check the pack.").format(
					frappe.bold(batch_id), frappe.format(existing.expiry_date, {"fieldtype": "Date"})
				),
				title=_("Batch already recorded"),
			)
		return {"name": existing.name, "batch_id": batch_id, "expiry_date": existing.expiry_date}

	batch = frappe.get_doc(
		{
			"doctype": "Batch",
			"item": item_code,
			"batch_id": batch_id,
			"expiry_date": expiry_date,
			"manufacturing_date": manufacturing_date,
			"supplier": supplier,
		}
	).insert()
	return {"name": batch.name, "batch_id": batch.batch_id, "expiry_date": batch.expiry_date}
