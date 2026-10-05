# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Round 5 (M-1): a new stock or price change is never stuck behind a backlog of retries.

Measured on the round-4 candidate (independent re-validation): each run took the 100 oldest due events
whatever their kind, so a new event waited behind every due retry — 250 retrying events delayed it by
about 4 minutes, 1000 by about 42 minutes, while the website showed the old stock or price.

Now each run sends the never-attempted events first and keeps RETRY_SLOTS for due retries (more when
there are fewer new events). Bound: a new event is sent within
ceil((never-attempted events queued before it + 1) / (BATCH_SIZE − RETRY_SLOTS)) runs, however many
events are retrying; retries progress by at least RETRY_SLOTS per run while any are due.
"""

import math
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from pharmacyos_erp.integration import outbox
from pharmacyos_erp.integration.outbox import BATCH_SIZE, MAX_ATTEMPTS, RETRY_SLOTS, TRANSIENT_PREFIX
from pharmacyos_erp.tests.utils import make_medicine, set_settings

FIELDS = (
	"name",
	"creation",
	"modified",
	"owner",
	"modified_by",
	"event_type",
	"status",
	"attempts",
	"dedupe_key",
	"payload",
	"last_error",
	"reference_doctype",
	"reference_name",
)


class Sender:
	"""requests.post stand-in: per event id, "ok", an HTTP status, "down" or "timeout"."""

	def __init__(self, outcome=None):
		self.outcome, self.sent = outcome or {}, []

	def __call__(self, url, data, headers, timeout):
		import requests

		event_id = headers["X-PharmacyOS-Event-Id"]
		self.sent.append(event_id)
		outcome = self.outcome.get(event_id, "ok")
		if outcome == "down":
			raise requests.ConnectionError("cloud unreachable")
		if outcome == "timeout":
			raise requests.Timeout("read timed out")

		class Response:
			status_code = 200 if outcome == "ok" else outcome

			def raise_for_status(self):
				if self.status_code >= 400:
					raise requests.HTTPError(f"{self.status_code} error", response=self)

		return Response()


class OutboxCase(IntegrationTestCase):
	def setUp(self):
		from frappe.utils.password import set_encrypted_password

		make_medicine("R5-OUTBOX")  # the events' reference (payloads are given, never built from it)
		frappe.db.delete("PharmacyOS Sync Event")
		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "s" * 40, "outbound_secret")
		frappe.db.commit()
		self.clock = 0

	def tearDown(self):
		frappe.db.delete("PharmacyOS Sync Event")
		set_settings(enable_outbound_events=0)
		frappe.db.commit()

	def queue(self, count, status="Pending", attempts=0, last_error=None, age_minutes=0, payload="{}"):
		"""`count` events, each created after every event queued before it (deterministic order)."""
		base = add_to_date(now_datetime(), days=-2)
		modified = add_to_date(now_datetime(), minutes=-age_minutes)
		rows, names = [], []
		for _ in range(count):
			self.clock += 1
			name = frappe.generate_hash(length=12)
			created = add_to_date(base, seconds=self.clock)
			rows.append(
				(
					name,
					created,
					modified if attempts or last_error else created,
					"Administrator",
					"Administrator",
					"catalog.changed",
					status,
					attempts,
					f"r5:{name}",
					payload,
					last_error,
					"Item",
					"R5-OUTBOX",
				)
			)
			names.append(name)
		frappe.db.bulk_insert("PharmacyOS Sync Event", FIELDS, rows)
		frappe.db.commit()
		return names

	def retrying(self, count, attempts=1):
		"""Failed `attempts` times, back-off long over: due now."""
		return self.queue(count, "Failed", attempts, "[unreachable] down", age_minutes=24 * 60)

	def run_once(self, sender=None):
		sender = sender or Sender()
		with patch("requests.post", side_effect=sender):
			outbox.process_outbox()
		return sender.sent

	def status(self, name):
		return frappe.db.get_value(
			"PharmacyOS Sync Event", name, ["status", "attempts", "last_error"], as_dict=True
		)


class TestFairness(OutboxCase):
	def test_empty_queue(self):
		self.assertEqual(outbox.due_events(), ([], []))
		self.assertEqual(self.run_once(), [])

	def test_a_new_event_goes_out_in_the_first_run_whatever_the_retry_backlog(self):
		for backlog in (0, 1, 99, 100, 120, 200, 250, 500, 1000):
			with self.subTest(retrying=backlog):
				frappe.db.delete("PharmacyOS Sync Event")
				frappe.db.commit()
				retries = self.retrying(backlog)
				[fresh] = self.queue(1)
				sent = self.run_once()
				self.assertIn(fresh, sent, f"new event held back by {backlog} retries")
				self.assertEqual(sent[0], fresh, "never-attempted events go first")
				self.assertEqual(len(sent), min(backlog + 1, BATCH_SIZE))
				self.assertEqual(sent[1:], retries[: BATCH_SIZE - 1], "retries oldest first")
				self.assertEqual(self.status(fresh).status, "Sent")

	def test_bound_with_a_backlog_of_new_events_and_retries(self):
		"""The documented bound, exactly: ceil((new events before it + 1) / (BATCH_SIZE − RETRY_SLOTS))."""
		for new_before, retrying in ((0, 300), (74, 300), (75, 300), (200, 300), (300, 1000), (120, 0)):
			with self.subTest(new_before=new_before, retrying=retrying):
				frappe.db.delete("PharmacyOS Sync Event")
				frappe.db.commit()
				retries = set(self.retrying(retrying))
				self.queue(new_before)
				[ours] = self.queue(1)
				lane = BATCH_SIZE - (RETRY_SLOTS if retrying else 0)
				bound = math.ceil((new_before + 1) / lane)
				runs = 0
				while self.status(ours).status != "Sent":
					runs += 1
					sent = self.run_once()
					retried = len([n for n in sent if n in retries])
					still_due = len(outbox.due_events()[1])
					if retrying:  # retries are never starved either
						self.assertGreaterEqual(retried, min(RETRY_SLOTS, retried + still_due))
					self.assertLessEqual(runs, bound, f"bound {bound} exceeded")
				self.assertEqual(runs, bound)

	def test_retries_use_the_capacity_new_events_leave(self):
		self.retrying(200)
		fresh = self.queue(10)
		sent = self.run_once()
		self.assertEqual(sent[:10], fresh)
		self.assertEqual(len(sent), BATCH_SIZE)

	def test_new_events_use_the_capacity_retries_leave(self):
		self.retrying(5)
		fresh = self.queue(150)
		sent = self.run_once()
		self.assertEqual(sent[:95], fresh[:95])
		self.assertEqual(len(sent), BATCH_SIZE)

	def test_order_is_deterministic(self):
		self.retrying(40)
		self.queue(30, status="Failed")  # failed before any attempt was recorded: still new
		self.assertEqual(outbox.due_events(), outbox.due_events())
		fresh, retries = outbox.due_events()
		self.assertEqual(len(fresh), 30)
		self.assertEqual(len(retries), 40)

	def test_revived_events_do_not_jump_ahead_of_new_ones(self):
		"""After an outage, exhausted events get fresh attempts — they stay behind new events."""
		self.queue(150, "Pending", 0, TRANSIENT_PREFIX + "down")
		[fresh] = self.queue(1)
		sent = self.run_once()
		self.assertEqual(sent[0], fresh)
		self.assertEqual(len(sent), BATCH_SIZE)

	def test_back_off_ages(self):
		now = now_datetime()
		cases = []
		for attempts in range(1, MAX_ATTEMPTS + 1):
			delay = min(2**attempts, 60)
			for age, due in ((delay + 1, True), (delay - 1, False)):
				[name] = self.queue(1, "Failed", attempts, "[unreachable] down", age_minutes=age)
				cases.append((name, attempts, due and attempts < MAX_ATTEMPTS))
		self.queue(3, "Sent", 1, None, age_minutes=600)
		self.queue(2, "Skipped", 0, None)
		fresh, retries = outbox.due_events(now)
		self.assertEqual(fresh, [])
		taken = {r.name for r in retries}
		for name, attempts, expected in cases:
			with self.subTest(attempts=attempts, due=expected):
				self.assertEqual(name in taken, expected)
				row = frappe.db.get_value(
					"PharmacyOS Sync Event", name, ["modified", "attempts"], as_dict=True
				)
				if attempts < MAX_ATTEMPTS:
					self.assertEqual(outbox.is_due(row, now), expected, "SQL and is_due agree")


class TestFailures(OutboxCase):
	def test_each_failure_kind_is_recorded_and_the_run_continues(self):
		names = self.queue(6)
		ok, bad_request, server_error, down, timeout, ok_after = names
		sender = Sender({bad_request: 400, server_error: 503, down: "down", timeout: "timeout"})
		sent = self.run_once(sender)
		self.assertEqual(sent, names, "every event was attempted in the same run")
		self.assertEqual(self.status(ok).status, "Sent")
		self.assertEqual(self.status(ok_after).status, "Sent")
		rejected = self.status(bad_request)
		self.assertEqual((rejected.status, rejected.attempts), ("Failed", 1))
		self.assertFalse(
			rejected.last_error.startswith(TRANSIENT_PREFIX), "a 400 is not a connectivity problem"
		)
		for name in (server_error, down, timeout):
			row = self.status(name)
			self.assertEqual((row.status, row.attempts), ("Failed", 1))
			self.assertTrue(row.last_error.startswith(TRANSIENT_PREFIX), row.last_error)

	def test_a_payload_that_cannot_be_built_fails_alone(self):
		names = self.queue(3, payload=None)
		broken = names[1]
		real = outbox.build_payload

		def build(event):
			if event.name == broken:
				raise ValueError("item vanished")
			return {"event": event.event_type, "event_id": event.name}

		with patch.object(outbox, "build_payload", side_effect=build):
			sent = self.run_once()
		self.assertNotIn(broken, sent)
		self.assertEqual(sent, [names[0], names[2]])
		row = self.status(broken)
		self.assertEqual((row.status, row.attempts), ("Failed", 1))
		self.assertTrue(row.last_error.startswith("[payload] "), row.last_error)
		self.assertTrue(callable(real))

	def test_a_new_change_is_not_absorbed_by_a_revived_event_with_a_frozen_payload(self):
		[frozen] = self.queue(1, "Pending", 0, TRANSIENT_PREFIX + "down", payload='{"old": 1}')
		key = frappe.db.get_value("PharmacyOS Sync Event", frozen, "dedupe_key")
		outbox.queue_event("catalog.changed", "Item", "R5-OUTBOX", key)
		self.assertEqual(frappe.db.count("PharmacyOS Sync Event", {"dedupe_key": key}), 2)
		[pending] = self.queue(1, payload=None)
		key = frappe.db.get_value("PharmacyOS Sync Event", pending, "dedupe_key")
		outbox.queue_event("catalog.changed", "Item", "R5-OUTBOX", key)
		self.assertEqual(frappe.db.count("PharmacyOS Sync Event", {"dedupe_key": key}), 1, "unsent: absorbed")
