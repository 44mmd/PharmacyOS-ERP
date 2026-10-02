"""Outbound events to the PharmacyOS backend (transactional outbox).

* Events are written as **PharmacyOS Sync Event** rows inside the same database transaction as the
  change that caused them, so a rolled-back document never emits an event.
* Nothing is recorded or sent unless *PharmacyOS Settings → Integration → Send Events* is enabled
  with an HTTPS endpoint. Out of the box no external system is contacted.
* Pending events are deduplicated by key (e.g. one `availability.changed` per item + warehouse), and
  the payload is computed **at send time**, so the receiver always gets current state — never a
  stale snapshot.
* Delivery: scheduler job every 5 minutes (`process_outbox`), JSON POST signed with HMAC-SHA256 over
  the raw body (`X-PharmacyOS-Signature: sha256=<hex>`), with `X-PharmacyOS-Event` / `-Event-Id`
  headers. Up to MAX_ATTEMPTS tries with exponential back-off (2, 4, 8 … minutes, capped at an
  hour), then the event stays `Failed` until an authorized user presses Retry.
* Offline behaviour: local sales, stock and accounting commit locally first; events simply wait in
  the queue while the internet is down and are delivered, in order, once it returns. The receiver
  de-duplicates by `X-PharmacyOS-Event-Id`, so a retried delivery never creates a second record.
"""

import hashlib
import hmac
import json
from datetime import timedelta

import frappe
from frappe.utils import cint, get_datetime, now_datetime

MAX_ATTEMPTS = 5
BATCH_SIZE = 100


def outbound_enabled() -> bool:
	settings = frappe.get_cached_doc("PharmacyOS Settings")
	return bool(cint(settings.enable_outbound_events) and settings.outbound_endpoint)


def queue_event(event_type: str, reference_doctype: str, reference_name: str, dedupe_key: str) -> None:
	if not outbound_enabled():
		return
	if frappe.db.exists(
		"PharmacyOS Sync Event",
		{"dedupe_key": dedupe_key, "status": ["in", ["Pending", "Failed"]], "attempts": ["<", MAX_ATTEMPTS]},
	):
		return  # the queued event is sent with current state, so one per key is enough
	frappe.get_doc(
		{
			"doctype": "PharmacyOS Sync Event",
			"event_type": event_type,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"dedupe_key": dedupe_key,
			"status": "Pending",
		}
	).insert(ignore_permissions=True)


# ------------------------------------------------------------------ doc_event handlers


def on_stock_ledger_entry(doc, method=None):
	"""Availability changed for a published item in a warehouse."""
	if not outbound_enabled():
		return
	if not frappe.get_cached_value("Item", doc.item_code, "pharmacyos_publish"):
		return
	queue_event(
		"availability.changed", "Item", doc.item_code, f"availability:{doc.item_code}:{doc.warehouse}"
	)


def on_sales_order(doc, method=None):
	if doc.get("pharmacyos_order_id"):
		queue_event("order.status_changed", "Sales Order", doc.name, f"order:{doc.name}")


def on_fulfilment_document(doc, method=None):
	"""Delivery Note / Sales Invoice against a PharmacyOS order changes that order's status."""
	if not outbound_enabled():
		return
	field = "against_sales_order" if doc.doctype == "Delivery Note" else "sales_order"
	orders = {row.get(field) for row in doc.items if row.get(field)}
	for so in orders:
		if frappe.db.get_value("Sales Order", so, "pharmacyos_order_id"):
			queue_event("order.status_changed", "Sales Order", so, f"order:{so}")


def on_item(doc, method=None):
	if doc.get("pharmacyos_publish") or (
		doc.get_doc_before_save() and doc.get_doc_before_save().get("pharmacyos_publish")
	):
		queue_event("catalog.changed", "Item", doc.name, f"catalog:{doc.name}")


# ------------------------------------------------------------------ payloads & delivery


def build_payload(event) -> dict:
	from pharmacyos_erp.api.v1.availability import sellable_qty
	from pharmacyos_erp.pharmacy.branches import branch_for_warehouse, get_branch_warehouses

	body = {
		"event": event.event_type,
		"event_id": event.name,
		"occurred_at": str(event.creation),
		"sent_at": str(now_datetime()),
	}
	if event.event_type == "availability.changed":
		_kind, item_code, warehouse = event.dedupe_key.split(":", 2)
		branch = branch_for_warehouse(warehouse)
		code = branch and frappe.db.get_value("Branch", branch, "pharmacyos_storefront_code")
		data = {"item_code": item_code, "branch_code": code}
		if code:
			has_batch = cint(frappe.get_cached_value("Item", item_code, "has_batch_no"))
			qty, nearest = sellable_qty(item_code, get_branch_warehouses(branch), has_batch)
			data.update({"qty": qty, "nearest_expiry": nearest})
		body["data"] = data
	elif event.event_type == "order.status_changed":
		so = frappe.db.get_value(
			"Sales Order",
			event.reference_name,
			["pharmacyos_order_id", "status", "docstatus", "per_delivered", "per_billed"],
			as_dict=True,
		)
		body["data"] = {"sales_order": event.reference_name, **(so or {})}
	elif event.event_type == "catalog.changed":
		body["data"] = {
			"item_code": event.reference_name,
			"published": cint(frappe.db.get_value("Item", event.reference_name, "pharmacyos_publish")),
		}
	return body


def sign(body: bytes, secret: str) -> str:
	return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def retry_delay(attempts: int) -> timedelta:
	return timedelta(minutes=min(2 ** cint(attempts), 60)) if attempts else timedelta(0)


def is_due(row, now=None) -> bool:
	return get_datetime(row.modified) + retry_delay(row.attempts) <= get_datetime(now or now_datetime())


def process_outbox() -> None:
	"""Scheduler entry point. Sends pending events; no-op unless outbound is enabled."""
	if not outbound_enabled():
		return
	import requests

	settings = frappe.get_cached_doc("PharmacyOS Settings")
	secret = settings.get_password("outbound_secret", raise_exception=False) or ""
	timeout = cint(settings.outbound_timeout) or 10
	now = now_datetime()
	events = frappe.get_all(
		"PharmacyOS Sync Event",
		filters={"status": ["in", ["Pending", "Failed"]], "attempts": ["<", MAX_ATTEMPTS]},
		fields=["name", "attempts", "modified"],
		order_by="creation asc",
		limit_page_length=BATCH_SIZE,
	)
	for row in events:
		if not is_due(row, now):
			continue
		event = frappe.get_doc("PharmacyOS Sync Event", row.name)
		payload = build_payload(event)
		body = json.dumps(payload, separators=(",", ":"), default=str).encode()
		headers = {
			"Content-Type": "application/json",
			"X-PharmacyOS-Event": event.event_type,
			"X-PharmacyOS-Event-Id": event.name,
			"X-PharmacyOS-Signature": sign(body, secret),
		}
		event.attempts = cint(event.attempts) + 1
		event.payload = json.dumps(payload, indent=1, default=str)
		try:
			response = requests.post(settings.outbound_endpoint, data=body, headers=headers, timeout=timeout)
			response.raise_for_status()
			event.status, event.sent_on, event.last_error = "Sent", now_datetime(), None
		except Exception as e:
			event.status = "Failed"
			event.last_error = str(e)[:500]
		event.save(ignore_permissions=True)
		frappe.db.commit()  # each delivery is independent


# ------------------------------------------------------------------ status & actions

STATUS_ROLES = ("System Manager", "Pharmacy Owner", "Pharmacy Manager")


def sync_status() -> dict:
	settings = frappe.get_cached_doc("PharmacyOS Settings")
	enabled = outbound_enabled()
	counts = dict(
		frappe.get_all(
			"PharmacyOS Sync Event",
			filters={"status": ["in", ["Pending", "Failed"]]},
			fields=["status", {"COUNT": "*", "as": "n"}],
			group_by="status",
			as_list=1,
		)
	)
	stuck = frappe.db.count("PharmacyOS Sync Event", {"status": "Failed", "attempts": [">=", MAX_ATTEMPTS]})
	last_sent = frappe.db.get_value(
		"PharmacyOS Sync Event", {"status": "Sent"}, "sent_on", order_by="sent_on desc"
	)
	last_failure = frappe.db.get_value(
		"PharmacyOS Sync Event",
		{"status": "Failed"},
		["modified", "last_error"],
		order_by="modified desc",
		as_dict=True,
	)
	connected = None
	if enabled:
		# the most recent delivery attempt decides whether the cloud is currently reachable
		connected = not (
			last_failure and (not last_sent or get_datetime(last_failure.modified) > get_datetime(last_sent))
		)
	if not enabled:
		state = "not_configured"
	elif stuck:
		state = "attention"
	elif connected is False:
		state = "offline"
	else:
		state = "online"
	return {
		"state": state,
		"enabled": enabled,
		"endpoint_configured": bool(settings.outbound_endpoint),
		"pending": cint(counts.get("Pending")) + cint(counts.get("Failed")) - stuck,
		"failed": stuck,
		"last_success": last_sent,
		"last_error": last_failure.last_error if (last_failure and connected is False) or stuck else None,
	}


@frappe.whitelist()
def get_sync_status() -> dict:
	frappe.only_for(STATUS_ROLES)
	return sync_status()


@frappe.whitelist(methods=["POST"])
def retry_failed() -> int:
	"""Give events that used up their attempts a fresh start and send now (audited by track_changes)."""
	frappe.only_for(("System Manager", "Pharmacy Owner"))
	names = frappe.get_all(
		"PharmacyOS Sync Event", filters={"status": "Failed", "attempts": [">=", MAX_ATTEMPTS]}, pluck="name"
	)
	for name in names:
		event = frappe.get_doc("PharmacyOS Sync Event", name)
		event.attempts = 0
		event.status = "Pending"
		event.save(ignore_permissions=True)
	if outbound_enabled():
		frappe.enqueue(
			"pharmacyos_erp.integration.outbox.process_outbox",
			queue="short",
			deduplicate=True,
			job_id="pharmacyos-outbox-retry",
		)
	return len(names)
