# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Flow F: sync interruption — offline, local transaction, queued event, retry, no duplicate."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime
from frappe.utils.password import set_encrypted_password

from pharmacyos_erp.integration import outbox
from pharmacyos_erp.tests.utils import (
	make_batch,
	make_medicine,
	make_sales_invoice,
	make_user,
	receive,
	set_settings,
)


class TestSyncInterruption(IntegrationTestCase):
	def setUp(self):
		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "s", "outbound_secret")
		frappe.db.delete("PharmacyOS Sync Event")

	def tearDown(self):
		set_settings(enable_outbound_events=0, outbound_endpoint=None)

	def _age(self, minutes):
		frappe.db.sql(
			"update `tabPharmacyOS Sync Event` set modified=%s", add_to_date(now_datetime(), minutes=-minutes)
		)

	def test_offline_sale_is_queued_retried_and_delivered_once(self):
		item = make_medicine("POS-SYNC-MED", item_name="Sync Test Medicine")
		batch = make_batch(item.name, "SY-0001", 300)
		frappe.db.set_value("Item", item.name, "pharmacyos_publish", 1)  # storefront item
		frappe.clear_document_cache("Item", item.name)
		receive(item.name, batch, 5)
		frappe.db.delete("PharmacyOS Sync Event")

		# 1. internet down: the sale still submits locally
		invoice = make_sales_invoice(item.name, batch, 1, submit=True)
		self.assertEqual(invoice.docstatus, 1)
		events = frappe.get_all("PharmacyOS Sync Event", fields=["name", "event_type", "status"])
		self.assertTrue(events, "the stock change must be queued for the cloud")
		with patch("requests.post", side_effect=ConnectionError("network is unreachable")):
			outbox.process_outbox()
		self.assertEqual(outbox.sync_status()["state"], "offline")
		failed = frappe.get_all("PharmacyOS Sync Event", filters={"status": "Failed"}, pluck="name")
		self.assertTrue(failed)

		# 2. back-off: an immediate second run does not hammer the endpoint
		calls = []
		with patch("requests.post", side_effect=lambda *a, **k: calls.append(1)):
			outbox.process_outbox()
		self.assertEqual(calls, [])

		# 3. connection restored after the back-off window: delivered, same event id
		self._age(10)
		sent = []

		class Ok:
			def raise_for_status(self):
				return None

		def fake_post(url, data, headers, timeout):
			sent.append(headers["X-PharmacyOS-Event-Id"])
			return Ok()

		with patch("requests.post", side_effect=fake_post):
			outbox.process_outbox()
			outbox.process_outbox()  # nothing left: no duplicate delivery
		self.assertEqual(sorted(sent), sorted(set(sent)))
		self.assertTrue(set(failed) <= set(sent))
		self.assertEqual(frappe.db.count("PharmacyOS Sync Event", {"status": ["!=", "Sent"]}), 0)
		status = outbox.sync_status()
		self.assertEqual(status["state"], "online")
		self.assertEqual(status["pending"], 0)

	def test_exhausted_events_need_manual_retry(self):
		item = make_medicine("POS-SYNC-X", item_name="Sync Retry Medicine").name
		outbox.queue_event("catalog.changed", "Item", item, "catalog:X")
		frappe.db.set_value(
			"PharmacyOS Sync Event",
			{"dedupe_key": "catalog:X"},
			{"status": "Failed", "attempts": outbox.MAX_ATTEMPTS},
		)
		self.assertEqual(outbox.sync_status()["state"], "attention")
		# a new change for the same key is not lost while the old event is exhausted
		outbox.queue_event("catalog.changed", "Item", item, "catalog:X")
		self.assertEqual(frappe.db.count("PharmacyOS Sync Event", {"dedupe_key": "catalog:X"}), 2)
		with patch("pharmacyos_erp.integration.outbox.frappe.enqueue"):
			self.assertEqual(outbox.retry_failed(), 1)
		self.assertEqual(frappe.db.count("PharmacyOS Sync Event", {"status": "Failed"}), 0)

	def test_back_off_grows(self):
		self.assertEqual(outbox.retry_delay(0).total_seconds(), 0)
		self.assertEqual(outbox.retry_delay(1).total_seconds(), 120)
		self.assertEqual(outbox.retry_delay(3).total_seconds(), 480)
		self.assertEqual(outbox.retry_delay(10).total_seconds(), 3600)

	def test_cashier_cannot_see_or_retry_sync(self):
		user = make_user("pos-cashier-sync@example.com", ["Cashier"])
		frappe.set_user(user)
		try:
			self.assertRaises(frappe.PermissionError, outbox.get_sync_status)
			self.assertRaises(frappe.PermissionError, outbox.retry_failed)
		finally:
			frappe.set_user("Administrator")
