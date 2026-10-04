"""Outbound events to the PharmacyOS backend (transactional outbox).

* Events are written as **PharmacyOS Sync Event** rows inside the same database transaction as the
  change that caused them, so a rolled-back document never emits an event.
* Nothing is recorded or sent unless *PharmacyOS Settings → Integration → Send Events* is enabled
  with an HTTPS endpoint. Out of the box no external system is contacted.
* Event identity is immutable: an event's payload (current state of the item / order, stamped with
  `computed_at`) is computed on its first delivery attempt and stored; every retry re-sends exactly
  the same bytes under the same `event_id`. A change that happens after an event's first attempt is
  queued as a new event with a later `computed_at`, so the receiver applies a state only when it is
  newer than the one it holds (a lost response followed by a retry can never carry a different state
  under an already-applied id).
* Events not yet attempted are deduplicated by key (one `availability.changed` per item + warehouse):
  their payload is computed when they are first sent, so they always carry the newest state.
* Delivery: scheduler job every minute (`process_outbox`), JSON POST signed with HMAC-SHA256 over
  the raw body (`X-PharmacyOS-Signature: sha256=<hex>`), with `X-PharmacyOS-Event` / `-Event-Id`
  headers. Up to MAX_ATTEMPTS tries with exponential back-off (2, 4, 8 … minutes, capped at an
  hour), then the event stays `Failed`. Events that failed only because the Cloud was unreachable
  get a fresh set of attempts as soon as a later delivery proves the connection is back (a long
  internet outage must not leave the website stale); events the Cloud rejected wait for an
  authorized user's Retry.
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


def outbound_endpoint(settings=None) -> str | None:
	"""Explicit endpoint, else the PharmacyOS Cloud events URL derived from the cloud address."""
	settings = settings or frappe.get_cached_doc("PharmacyOS Settings")
	if settings.outbound_endpoint:
		return settings.outbound_endpoint
	if settings.get("cloud_base_url"):
		return settings.cloud_base_url.rstrip("/") + "/integrations/erp/events"
	return None


def outbound_enabled() -> bool:
	settings = frappe.get_cached_doc("PharmacyOS Settings")
	return bool(cint(settings.enable_outbound_events) and outbound_endpoint(settings))


def queue_event(event_type: str, reference_doctype: str, reference_name: str, dedupe_key: str) -> None:
	if not outbound_enabled():
		return
	if frappe.db.exists(
		"PharmacyOS Sync Event", {"dedupe_key": dedupe_key, "status": "Pending", "attempts": 0}
	):
		return  # not sent yet: its payload is computed at first send, so it will carry this change too
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
	if not outbound_enabled():
		return
	if doc.get("pharmacyos_order_id"):
		queue_event("order.status_changed", "Sales Order", doc.name, f"order:{doc.name}")
	if method in ("on_submit", "on_cancel"):
		# reservations change sellable stock without a stock ledger entry: tell the storefront
		for row in doc.items:
			if row.warehouse and frappe.get_cached_value("Item", row.item_code, "pharmacyos_publish"):
				queue_event(
					"availability.changed",
					"Item",
					row.item_code,
					f"availability:{row.item_code}:{row.warehouse}",
				)


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
	if doc.get("pharmacyos_publish"):
		# with its sellable stock: stock received before the item was published sent no event
		queue_catalog(doc.name)
	elif doc.get_doc_before_save() and doc.get_doc_before_save().get("pharmacyos_publish"):
		queue_event("catalog.changed", "Item", doc.name, f"catalog:{doc.name}")


def queue_catalog(item_code: str) -> None:
	"""A published item's public catalog entry (names, barcodes, ERP price) changed.

	Its sellable stock per branch is queued too: stock received before the item was published sent no
	availability event, and the website takes an item without an ERP price off sale (stock 0), so a
	newly published or re-priced item needs its current stock again.
	"""
	if not item_code or not frappe.db.get_value("Item", item_code, "pharmacyos_publish"):
		return
	queue_event("catalog.changed", "Item", item_code, f"catalog:{item_code}")
	for warehouse in frappe.get_all(
		"Branch", filters={"pharmacyos_warehouse": ["is", "set"]}, pluck="pharmacyos_warehouse"
	):
		queue_event("availability.changed", "Item", item_code, f"availability:{item_code}:{warehouse}")


def on_item_price(doc, method=None):
	"""doc_event (on_update / on_trash) for Item Price: the ERP price is authoritative for the website.

	Changing, adding or deleting the price of a published item in the catalog's selling price list
	queues a `catalog.changed` event for that item — no Item save needed. The event's payload is the
	item's catalog entry read when it is first sent (so it carries the newest price); a price change
	after an event was first attempted is queued as a new event with a later `computed_at`, and the
	Cloud applies a state only when it is newer than the one it holds.
	"""
	if not outbound_enabled():
		return
	from pharmacyos_erp.api.v1.catalog import selling_price_list

	price_list = selling_price_list()
	before = doc.get_doc_before_save() if method == "on_update" else None
	for row in (doc, before):
		if row is not None and row.get("price_list") == price_list:
			queue_catalog(row.get("item_code"))


def on_price_list(doc, method=None):
	"""doc_event (on_update) for Price List: enabling/disabling the catalog's list changes every price."""
	from pharmacyos_erp.api.v1.catalog import selling_price_list

	if doc.name != selling_price_list() or not outbound_enabled():
		return
	before = doc.get_doc_before_save()
	if (
		before is not None
		and all(cint(before.get(f)) == cint(doc.get(f)) for f in ("enabled", "selling"))
		and before.get("currency") == doc.get("currency")
	):
		return
	for code in frappe.get_all("Item", filters={"pharmacyos_publish": 1}, pluck="name"):
		queue_catalog(code)


PRICE_DATE_KEY = "pharmacyos_price_validity_date"


def queue_price_validity_changes(today=None) -> int:
	"""Scheduler (every minute): when the date changes, re-send items whose price starts or ends.

	An Item Price becomes valid on its `valid_from` and stops being valid the day after its
	`valid_upto` — at midnight (ERP time zone, Asia/Baghdad), with no document saved, so no doc_event
	fires. The last date processed is stored (Default Value); every date boundary since then is covered,
	so a server that was off over midnight (or several) catches up on its first run. Nothing to do
	while the date is unchanged. Returns the number of items queued.
	"""
	from frappe.utils import add_days, getdate, nowdate

	from pharmacyos_erp.api.v1.catalog import selling_price_list

	today = getdate(today or nowdate())
	stored = frappe.db.get_default(PRICE_DATE_KEY)
	last = getdate(stored) if stored else None
	if last == today:
		return 0
	since = last if last and last < today else add_days(today, -1)
	codes = frappe.db.sql_list(
		"""
		select distinct ip.item_code
		from `tabItem Price` ip
		join `tabItem` i on i.name = ip.item_code and i.pharmacyos_publish = 1
		where ip.price_list = %(price_list)s
			and ((ip.valid_from > %(since)s and ip.valid_from <= %(today)s)
				or (ip.valid_upto >= %(since)s and ip.valid_upto < %(today)s))
		""",
		{"price_list": selling_price_list(), "since": since, "today": today},
	)
	for code in codes:
		queue_catalog(code)
	frappe.db.set_default(PRICE_DATE_KEY, str(today))  # committed with the events (scheduler job)
	return len(codes)


def on_selling_settings(doc, method=None):
	"""The catalog's selling price list changed: every published item now has a different price."""
	before = doc.get_doc_before_save()
	if before is not None and before.get("selling_price_list") == doc.get("selling_price_list"):
		return
	if not outbound_enabled():
		return
	for code in frappe.get_all("Item", filters={"pharmacyos_publish": 1}, pluck="name"):
		queue_event("catalog.changed", "Item", code, f"catalog:{code}")


# ------------------------------------------------------------------ payloads & delivery


def build_payload(event) -> dict:
	from pharmacyos_erp.api.v1.availability import sellable_qty
	from pharmacyos_erp.pharmacy.branches import branch_for_warehouse, get_branch_warehouses

	body = {
		"event": event.event_type,
		"event_id": event.name,
		"occurred_at": str(event.creation),
		# version of the state below: receivers apply it only if newer than what they hold
		"computed_at": now_datetime().isoformat(),
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
		from pharmacyos_erp.api.v1.catalog import catalog_item

		published = cint(frappe.db.get_value("Item", event.reference_name, "pharmacyos_publish"))
		body["data"] = {
			"item_code": event.reference_name,
			"published": published,
			# public catalog fields only (names, barcodes, selling price) — never costs or margins
			"item": catalog_item(event.reference_name) if published else None,
		}
	return body


def sign(body: bytes, secret: str) -> str:
	return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def retry_delay(attempts: int) -> timedelta:
	return timedelta(minutes=min(2 ** cint(attempts), 60)) if attempts else timedelta(0)


def is_due(row, now=None) -> bool:
	return get_datetime(row.modified) + retry_delay(row.attempts) <= get_datetime(now or now_datetime())


TRANSIENT_PREFIX = "[unreachable] "


def _describe_failure(error: Exception) -> str:
	"""Error text, marked transient when the Cloud could not be reached or answered 5xx/429."""
	import requests

	response = getattr(error, "response", None)
	status = getattr(response, "status_code", None)
	transient = isinstance(error, (requests.ConnectionError, requests.Timeout, ConnectionError)) or (
		status is not None and (status >= 500 or status == 429)
	)
	return (TRANSIENT_PREFIX if transient else "") + str(error)[:480]


def revive_unreachable_events() -> int:
	"""Give exhausted events that failed only for connectivity a fresh set of attempts."""
	names = frappe.get_all(
		"PharmacyOS Sync Event",
		filters={
			"status": "Failed",
			"attempts": [">=", MAX_ATTEMPTS],
			"last_error": ["like", TRANSIENT_PREFIX + "%"],
		},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value("PharmacyOS Sync Event", name, {"attempts": 0, "status": "Pending"})
	return len(names)


def event_body(event) -> bytes:
	"""The exact bytes of an event. Computed once, then frozen: retries re-send the same state."""
	if not event.payload:
		event.payload = json.dumps(build_payload(event), separators=(",", ":"), default=str)
	return event.payload.encode()


def process_outbox() -> None:
	"""Scheduler entry point. Sends pending events; no-op unless outbound is enabled."""
	if not outbound_enabled():
		return
	import requests

	settings = frappe.get_cached_doc("PharmacyOS Settings")
	from pharmacyos_erp.pharmacyos.doctype.pharmacyos_settings.pharmacyos_settings import is_insecure_url

	endpoint = outbound_endpoint(settings)
	if is_insecure_url(endpoint):
		return
	secret = settings.get_password("outbound_secret", raise_exception=False) or ""
	timeout = cint(settings.outbound_timeout) or 10
	now = now_datetime()
	# only events that are due: events waiting out their back-off must never fill the batch and hold
	# back newer ones (e.g. a price expiry behind a hundred failed deliveries); same delay as is_due
	events = frappe.db.sql(
		"""
		select name, attempts, modified
		from `tabPharmacyOS Sync Event`
		where status in ('Pending', 'Failed') and attempts < %(max)s
			and (attempts = 0
				or timestampadd(minute, least(power(2, attempts), 60), modified) <= %(now)s)
		order by creation asc
		limit %(limit)s
		""",
		{"max": MAX_ATTEMPTS, "now": now, "limit": BATCH_SIZE},
		as_dict=True,
	)
	delivered = False
	for row in events:
		if not is_due(row, now):
			continue
		event = frappe.get_doc("PharmacyOS Sync Event", row.name)
		body = event_body(event)
		headers = {
			"Content-Type": "application/json",
			"X-PharmacyOS-Event": event.event_type,
			"X-PharmacyOS-Event-Id": event.name,
			"X-PharmacyOS-Signature": sign(body, secret),
		}
		event.attempts = cint(event.attempts) + 1
		try:
			response = requests.post(endpoint, data=body, headers=headers, timeout=timeout)
			response.raise_for_status()
			event.status, event.sent_on, event.last_error = "Sent", now_datetime(), None
			delivered = True
		except Exception as e:
			event.status = "Failed"
			event.last_error = _describe_failure(e)
		event.save(ignore_permissions=True)
		frappe.db.commit()  # each delivery is independent
	if delivered and revive_unreachable_events():
		frappe.db.commit()


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
		"endpoint_configured": bool(outbound_endpoint(settings)),
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
