"""Online orders from PharmacyOS → ERPNext Sales Orders (idempotent).

Idempotency: `Sales Order.pharmacyos_order_id` is DB-unique. The canonical request payload is hashed
(SHA-256) and stored. Re-sending the same order ID with the same payload returns the existing order
(`created: false`); the same ID with a different payload is rejected as a conflict (HTTP 409).

Prices come from ERPNext price lists (rates in the request are ignored); the response returns
ERPNext's totals so the backend can reconcile. The order is submitted, which reserves stock
(Bin.reserved_qty) and reduces the sellable quantity reported by `availability`.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, nowdate

from pharmacyos_erp.api.v1 import get_branch_by_code, require_integration
from pharmacyos_erp.api.v1.availability import sellable_qty
from pharmacyos_erp.api.v1.customers import ensure_customer
from pharmacyos_erp.pharmacy.branches import get_branch_warehouses


class OrderConflictError(frappe.DuplicateEntryError):
	"""Same order ID, different payload (HTTP 409)."""


def payload_hash(payload: dict) -> str:
	canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
	return hashlib.sha256(canonical.encode()).hexdigest()


def _normalise(order_id, branch_code, items, customer, delivery_date, notes) -> dict:
	if isinstance(items, str):
		items = json.loads(items)
	if isinstance(customer, str):
		customer = json.loads(customer)
	if not order_id or not str(order_id).strip():
		frappe.throw(_("order_id is required."))
	if not items:
		frappe.throw(_("An order needs at least one item."))
	clean_items = []
	for row in items:
		qty = flt(row.get("qty"))
		if not row.get("item_code") or qty <= 0:
			frappe.throw(_("Each item needs item_code and a positive qty."))
		clean_items.append({"item_code": str(row["item_code"]), "qty": qty})
	clean_items.sort(key=lambda r: r["item_code"])
	return {
		"order_id": str(order_id).strip(),
		"branch_code": branch_code,
		"items": clean_items,
		"customer": {k: (customer or {}).get(k) for k in ("id", "name", "phone", "email")},
		"delivery_date": delivery_date,
		"notes": notes,
	}


def _response(so_name: str, created: bool) -> dict:
	so = frappe.get_doc("Sales Order", so_name)
	return {
		"created": created,
		"order_id": so.pharmacyos_order_id,
		"sales_order": so.name,
		"status": so.status,
		"currency": so.currency,
		"grand_total": flt(so.grand_total),
		"items": [
			{"item_code": i.item_code, "qty": flt(i.qty), "rate": flt(i.rate), "amount": flt(i.amount)}
			for i in so.items
		],
	}


@frappe.whitelist(methods=["POST"])
def create_order(
	order_id: str,
	branch_code: str,
	items: str | list,
	customer: str | dict,
	delivery_date: str | None = None,
	notes: str | None = None,
) -> dict:
	require_integration()
	payload = _normalise(order_id, branch_code, items, customer, delivery_date, notes)
	digest = payload_hash(payload)

	existing = frappe.db.get_value(
		"Sales Order",
		{"pharmacyos_order_id": payload["order_id"]},
		["name", "pharmacyos_payload_hash"],
		as_dict=True,
	)
	if existing:
		if existing.pharmacyos_payload_hash != digest:
			raise OrderConflictError(
				_("Order {0} was already received with different contents.").format(payload["order_id"])
			)
		return _response(existing.name, created=False)

	branch = get_branch_by_code(branch_code)
	warehouses = get_branch_warehouses(branch.name)

	# validate items and availability before creating anything
	codes = [r["item_code"] for r in payload["items"]]
	items_meta = {
		i.name: i
		for i in frappe.get_all(
			"Item",
			filters={"name": ["in", codes]},
			fields=["name", "pharmacyos_publish", "disabled", "has_batch_no", "is_stock_item"],
		)
	}
	shortages = []
	for row in payload["items"]:
		meta = items_meta.get(row["item_code"])
		if not meta or not meta.pharmacyos_publish or meta.disabled:
			frappe.throw(
				_("Item {0} is not available for online orders.").format(row["item_code"]),
				frappe.DoesNotExistError,
			)
		if meta.is_stock_item:
			available, _nearest = sellable_qty(row["item_code"], warehouses, cint(meta.has_batch_no))
			if available < row["qty"]:
				shortages.append(
					{"item_code": row["item_code"], "requested": row["qty"], "available": available}
				)
	if shortages:
		frappe.throw(
			_("Insufficient stock for: {0}").format(
				", ".join(f"{s['item_code']} ({s['available']:g}/{s['requested']:g})" for s in shortages)
			),
			title=_("Insufficient stock"),
		)

	customer_name = ensure_customer(
		payload["customer"].get("id"),
		payload["customer"].get("name"),
		payload["customer"].get("phone"),
		payload["customer"].get("email"),
	)
	company = frappe.db.get_value("Warehouse", branch.pharmacyos_warehouse, "company")
	delivery = payload["delivery_date"] or add_days(nowdate(), 1)
	so = frappe.new_doc("Sales Order")
	so.update(
		{
			"customer": customer_name,
			"company": company,
			"transaction_date": nowdate(),
			"delivery_date": delivery,
			"po_no": payload["order_id"],
			"pharmacyos_order_id": payload["order_id"],
			"pharmacyos_payload_hash": digest,
			"set_warehouse": warehouses[0] if len(warehouses) == 1 else branch.pharmacyos_warehouse,
			"order_type": "Sales",
		}
	)
	if so.meta.has_field("branch"):
		so.branch = branch.name
	for row in payload["items"]:
		so.append(
			"items",
			{
				"item_code": row["item_code"],
				"qty": row["qty"],
				"delivery_date": delivery,
				"warehouse": warehouses[0] if len(warehouses) == 1 else branch.pharmacyos_warehouse,
			},
		)
	if payload["notes"]:
		so.add_comment("Comment", text=frappe.utils.escape_html(payload["notes"]))
	try:
		so.insert()
		so.submit()
	except frappe.DuplicateEntryError:
		# a concurrent request with the same order ID won the race
		frappe.db.rollback()
		existing = frappe.db.get_value(
			"Sales Order",
			{"pharmacyos_order_id": payload["order_id"]},
			["name", "pharmacyos_payload_hash"],
			as_dict=True,
		)
		if existing and existing.pharmacyos_payload_hash == digest:
			return _response(existing.name, created=False)
		raise OrderConflictError(
			_("Order {0} was already received with different contents.").format(payload["order_id"])
		)
	return _response(so.name, created=True)


@frappe.whitelist(methods=["GET"])
def get_order_status(order_id: str) -> dict:
	require_integration()
	name = frappe.db.get_value("Sales Order", {"pharmacyos_order_id": order_id})
	if not name:
		frappe.throw(_("Unknown order {0}.").format(order_id), frappe.DoesNotExistError)
	so = frappe.get_doc("Sales Order", name)
	invoices = frappe.get_all(
		"Sales Invoice Item", filters={"sales_order": name, "docstatus": 1}, pluck="parent", distinct=True
	)
	deliveries = frappe.get_all(
		"Delivery Note Item",
		filters={"against_sales_order": name, "docstatus": 1},
		pluck="parent",
		distinct=True,
	)
	return {
		"order_id": order_id,
		"sales_order": name,
		"status": so.status,
		"docstatus": so.docstatus,
		"per_delivered": flt(so.per_delivered),
		"per_billed": flt(so.per_billed),
		"grand_total": flt(so.grand_total),
		"currency": so.currency,
		"invoices": invoices,
		"deliveries": deliveries,
	}
