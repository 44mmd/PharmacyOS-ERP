"""Disposing of expired (or damaged, recalled) stock — auditable, with ERPNext's own stock movement.

A disposal is a submitted Stock Entry of type *Expired Stock Disposal* (purpose Material Issue): the batch
quantity leaves the warehouse through the stock ledger, its value goes to the company's stock adjustment
account, and the document keeps who disposed of what, when, how much and why (reason + note). Nothing is
deleted; a disposal can only be undone by cancelling that Stock Entry (a stock manager's right), which
returns the units as the same — still expired, never sellable — batch.

Rights: the stock roles (Pharmacy Owner, Pharmacy Manager, Inventory Manager — DISPOSAL_ROLES) that may also
create and submit Stock Entries; the documents are inserted and submitted as that user, never with raised
rights. Counter staff, buyers and the accountant cannot.

Expired stock never becomes sellable again: ERPNext refuses expired batches in every sale, and
`guard_batch_expiry` never lets an expired batch be re-dated (by anyone), and lets only the owner correct a
not-yet-expired batch's date later (recorded in the Batch's version history).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from pharmacyos_erp.permissions import require_pharmacy_role

DISPOSAL_TYPE = "Expired Stock Disposal"
REASONS = ("Expired", "Damaged", "Recalled", "Other")
REASON_FIELD = "pharmacyos_disposal_reason"
EXPIRY_CHANGE_ROLES = ("Pharmacy Owner", "System Manager")
# who may write stock off — whatever other stock rights a role carries
DISPOSAL_ROLES = ("Pharmacy Owner", "Pharmacy Manager", "Inventory Manager", "System Manager")


def ensure_disposal_type() -> None:
	if not frappe.db.exists("Stock Entry Type", DISPOSAL_TYPE):
		frappe.get_doc({"doctype": "Stock Entry Type", "name": DISPOSAL_TYPE, "purpose": "Material Issue"}).insert(
			ignore_permissions=True
		)


def _batch_qty(item_code: str, batch: str, warehouse: str) -> float:
	from pharmacyos_erp.pharmacy.expiry import get_batch_stock

	return sum(flt(r["qty"]) for r in get_batch_stock([warehouse], item_code) if r["batch_no"] == batch)


@frappe.whitelist()
def can_dispose() -> int:
	return cint(
		bool(set(frappe.get_roles()) & set(DISPOSAL_ROLES))
		and frappe.has_permission("Stock Entry", "create")
		and frappe.has_permission("Stock Entry", "submit")
	)


@frappe.whitelist(methods=["POST"])
def dispose_batch(item_code: str, batch: str, warehouse: str, qty: float, reason: str, note: str | None = None) -> dict:
	"""Write off `qty` units of one batch in one warehouse. Returns the submitted Stock Entry."""
	require_pharmacy_role()
	if not can_dispose():
		frappe.throw(_("You are not allowed to dispose of stock."), frappe.PermissionError)
	if reason not in REASONS:
		frappe.throw(_("Choose a reason for the disposal."))
	note = (note or "").strip()
	if reason == "Other" and len(note) < 3:
		frappe.throw(_("Describe why the stock is disposed of."))
	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Quantity must be greater than zero."))
	batch_doc = frappe.get_doc("Batch", batch)
	if batch_doc.item != item_code:
		frappe.throw(_("Batch {0} is not a batch of {1}.").format(batch_doc.batch_id, item_code))
	if reason == "Expired" and not (batch_doc.expiry_date and getdate(batch_doc.expiry_date) < getdate(nowdate())):
		frappe.throw(_("Batch {0} has not expired yet. Choose another reason if it must be disposed of.").format(batch_doc.batch_id))
	frappe.has_permission("Warehouse", "read", warehouse, throw=True)

	# one disposal at a time per item/warehouse: lock the stock row, then read what is really there
	frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "name", for_update=True)
	available = _batch_qty(item_code, batch, warehouse)
	if qty > available + 1e-9:
		frappe.throw(_("Only {0} of batch {1} are in {2}.").format(frappe.format(available), batch_doc.batch_id, warehouse))

	ensure_disposal_type()
	company = frappe.get_cached_value("Warehouse", warehouse, "company")
	remarks = _("Disposal ({0}) of batch {1}, expiry {2}.").format(_(reason), batch_doc.batch_id, batch_doc.expiry_date or "-")
	if note:
		remarks += " " + note[:500]
	entry = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": DISPOSAL_TYPE,
			"purpose": "Material Issue",
			"company": company,
			REASON_FIELD: reason,
			"remarks": remarks,
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"s_warehouse": warehouse,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
				}
			],
		}
	)
	entry.insert()
	entry.submit()
	return {"name": entry.name, "qty": qty, "batch_id": batch_doc.batch_id, "warehouse": warehouse, "reason": reason}


@frappe.whitelist()
def get_disposals(limit: int = 50) -> list[dict]:
	"""The latest disposals: what, how much, why, by whom and when (Stock Entry permissions apply)."""
	require_pharmacy_role()
	entries = frappe.get_list(
		"Stock Entry",
		filters={"stock_entry_type": DISPOSAL_TYPE, "docstatus": 1},
		fields=["name", "posting_date", "posting_time", "owner", REASON_FIELD, "remarks"],
		order_by="creation desc",
		limit_page_length=min(max(cint(limit), 1), 200),
	)
	if not entries:
		return []
	lines = frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": ["in", [e.name for e in entries]]},
		fields=["parent", "item_code", "item_name", "qty", "s_warehouse", "batch_no", "serial_and_batch_bundle"],
	)
	batch_ids = {b.name: b.batch_id for b in frappe.get_all("Batch", filters={"name": ["in", [l.batch_no for l in lines if l.batch_no] or [""]]}, fields=["name", "batch_id"])}
	by_entry = {}
	for line in lines:
		by_entry.setdefault(line.parent, []).append(
			{"item_code": line.item_code, "item_name": line.item_name, "qty": flt(line.qty), "warehouse": line.s_warehouse, "batch_id": batch_ids.get(line.batch_no, line.batch_no)}
		)
	for entry in entries:
		entry["reason"] = entry.pop(REASON_FIELD, None)
		entry["user"] = frappe.utils.get_fullname(entry.owner)
		entry["items"] = by_entry.get(entry.name, [])
	return entries


def guard_batch_expiry(doc, method=None):
	"""Batch.validate: moving an existing batch's expiry date later (or clearing it) would make expired
	stock sellable again. A batch that has already expired is never re-dated — by anyone: expired stock is
	disposed of. Before it expires, only the pharmacy owner may correct a mistyped date later (the Batch
	keeps a version history, so the correction is recorded)."""
	if doc.is_new():
		return
	before = frappe.db.get_value("Batch", doc.name, "expiry_date")
	if not before:
		return
	after = doc.expiry_date
	if after and getdate(after) <= getdate(before):
		return
	if getdate(before) < getdate(nowdate()):
		frappe.throw(
			_("Batch {0} expired on {1}. An expired batch cannot be re-dated: dispose of it instead.").format(
				frappe.bold(doc.batch_id or doc.name), frappe.format(before, {"fieldtype": "Date"})
			),
			frappe.PermissionError,
		)
	if set(frappe.get_roles()) & set(EXPIRY_CHANGE_ROLES):
		return
	frappe.throw(
		_("Only the pharmacy owner can move a batch's expiry date later. Expired stock is disposed of, not re-dated."),
		frappe.PermissionError,
	)
