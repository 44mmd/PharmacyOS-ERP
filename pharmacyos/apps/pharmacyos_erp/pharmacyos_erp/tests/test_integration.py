# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Stage E: integration API (permissions, catalog, availability, idempotent orders) and outbox."""

import hashlib
import hmac
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate
from frappe.utils.password import set_encrypted_password

from pharmacyos_erp.api.v1 import availability, catalog, orders
from pharmacyos_erp.integration import outbox
from pharmacyos_erp.tests.utils import WAREHOUSE, make_batch, make_medicine, make_user, receive, set_settings

BRANCH = "POS API Branch"
CODE = "API-TEST-01"


def ensure_api_branch():
	if not frappe.db.exists("Branch", BRANCH):
		frappe.get_doc(
			{
				"doctype": "Branch",
				"branch": BRANCH,
				"pharmacyos_warehouse": WAREHOUSE,
				"pharmacyos_storefront_code": CODE,
			}
		).insert()


def published_medicine(code):
	item = make_medicine(code, item_name=f"API {code}")
	frappe.db.set_value("Item", code, "pharmacyos_publish", 1)
	return item


class TestIntegrationAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_api_branch()
		cls.item = published_medicine("POS-API-MED")
		cls.b_expired = make_batch(cls.item.name, "API-EXP", -5)
		cls.b_good = make_batch(cls.item.name, "API-GOOD", 300)
		receive(cls.item.name, cls.b_expired, 4, posting_date=add_days(nowdate(), -30))
		receive(cls.item.name, cls.b_good, 10)
		cls.unpublished = make_medicine("POS-API-HIDDEN")
		cls.api_user = make_user(
			"pos-integration@example.test", ["PharmacyOS Integration", "Sales User", "Stock User"]
		)

	def as_api(self):
		frappe.set_user(self.api_user)
		self.addCleanup(frappe.set_user, "Administrator")

	def test_staff_roles_cannot_call_api(self):
		user = make_user("pos-staff@example.test", ["Pharmacy Manager", "Sales Manager", "Stock Manager"])
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				catalog.get_catalog()
			with self.assertRaises(frappe.PermissionError):
				orders.create_order("X-1", CODE, [{"item_code": self.item.name, "qty": 1}], {"id": "c1"})
		finally:
			frappe.set_user("Administrator")

	def test_catalog_only_published_with_cursor(self):
		self.as_api()
		data = catalog.get_catalog(limit=500)
		codes = {i["item_code"] for i in data["items"]}
		self.assertIn(self.item.name, codes)
		self.assertNotIn(self.unpublished.name, codes)
		page = catalog.get_catalog(limit=1)
		if page["has_more"]:
			nxt = catalog.get_catalog(cursor=page["next_cursor"], limit=1)
			self.assertNotEqual(page["items"][0]["item_code"], nxt["items"][0]["item_code"])

	def test_availability_excludes_expired_batches(self):
		self.as_api()
		rows = availability.get_availability(branch_code=CODE, item_codes=self.item.name)["availability"]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["qty"], 10)  # 4 expired units are not sellable
		self.assertEqual(rows[0]["branch_code"], CODE)

	def test_order_is_idempotent_and_reserves_stock(self):
		self.as_api()
		before = availability.get_availability(branch_code=CODE, item_codes=self.item.name)["availability"][
			0
		]["qty"]
		args = (
			"ORD-IDEMP-1",
			CODE,
			[{"item_code": self.item.name, "qty": 2}],
			{"id": "cust-1", "name": "Test Buyer"},
		)
		first = orders.create_order(*args)
		second = orders.create_order(*args)
		self.assertTrue(first["created"])
		self.assertFalse(second["created"])
		self.assertEqual(first["sales_order"], second["sales_order"])
		self.assertEqual(frappe.db.count("Sales Order", {"pharmacyos_order_id": "ORD-IDEMP-1"}), 1)
		after = availability.get_availability(branch_code=CODE, item_codes=self.item.name)["availability"][0][
			"qty"
		]
		self.assertEqual(before - after, 2)  # submitted order reserves stock
		status = orders.get_order_status("ORD-IDEMP-1")
		self.assertEqual(status["sales_order"], first["sales_order"])

	def test_same_order_id_different_payload_conflicts(self):
		self.as_api()
		orders.create_order(
			"ORD-CONFLICT-1", CODE, [{"item_code": self.item.name, "qty": 1}], {"id": "cust-2"}
		)
		with self.assertRaises(orders.OrderConflictError):
			orders.create_order(
				"ORD-CONFLICT-1", CODE, [{"item_code": self.item.name, "qty": 3}], {"id": "cust-2"}
			)

	def test_insufficient_and_unpublished_rejected(self):
		self.as_api()
		with self.assertRaises(frappe.ValidationError):
			orders.create_order(
				"ORD-SHORT-1", CODE, [{"item_code": self.item.name, "qty": 999}], {"id": "cust-3"}
			)
		with self.assertRaises(frappe.DoesNotExistError):
			orders.create_order(
				"ORD-HIDDEN-1", CODE, [{"item_code": self.unpublished.name, "qty": 1}], {"id": "cust-3"}
			)
		self.assertFalse(frappe.db.exists("Sales Order", {"pharmacyos_order_id": "ORD-SHORT-1"}))

	def test_customer_is_linked_once(self):
		self.as_api()
		orders.create_order(
			"ORD-CUST-1",
			CODE,
			[{"item_code": self.item.name, "qty": 1}],
			{"id": "cust-9", "name": "Repeat Buyer"},
		)
		orders.create_order(
			"ORD-CUST-2",
			CODE,
			[{"item_code": self.item.name, "qty": 1}],
			{"id": "cust-9", "name": "Repeat Buyer"},
		)
		self.assertEqual(frappe.db.count("Customer", {"pharmacyos_customer_id": "cust-9"}), 1)

	def test_payload_hash_is_order_independent(self):
		a = orders._normalise(
			"A", CODE, [{"item_code": "x", "qty": 1}, {"item_code": "y", "qty": 2}], {"id": "c"}, None, None
		)
		b = orders._normalise(
			"A", CODE, [{"item_code": "y", "qty": 2}, {"item_code": "x", "qty": 1}], {"id": "c"}, None, None
		)
		self.assertEqual(orders.payload_hash(a), orders.payload_hash(b))


class TestOutbox(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_api_branch()
		cls.item = published_medicine("POS-API-OUTBOX")

	def tearDown(self):
		set_settings(enable_outbound_events=0)

	def test_nothing_recorded_when_disabled(self):
		set_settings(enable_outbound_events=0)
		before = frappe.db.count("PharmacyOS Sync Event")
		receive(self.item.name, make_batch(self.item.name, "OB-OFF", 300), 3)
		self.assertEqual(frappe.db.count("PharmacyOS Sync Event"), before)

	def test_events_deduplicated_and_delivered_signed(self):
		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "test-secret", "outbound_secret")
		key = f"availability:{self.item.name}:{WAREHOUSE}"
		frappe.db.delete("PharmacyOS Sync Event", {"dedupe_key": key})
		batch = make_batch(self.item.name, "OB-ON", 300)
		receive(self.item.name, batch, 3)
		receive(self.item.name, batch, 2)
		self.assertEqual(
			frappe.db.count("PharmacyOS Sync Event", {"dedupe_key": key, "status": "Pending"}), 1
		)

		sent = []

		class Ok:
			def raise_for_status(self):
				return None

		def fake_post(url, data, headers, timeout):
			sent.append((url, data, headers))
			return Ok()

		with patch("requests.post", side_effect=fake_post):
			outbox.process_outbox()

		ours = [
			s
			for s in sent
			if json.loads(s[1])["event"] == "availability.changed"
			and json.loads(s[1])["data"].get("item_code") == self.item.name
		]
		self.assertTrue(ours)
		url, body, headers = ours[0]
		self.assertEqual(url, "https://pharmacyos.invalid/hook")
		expected = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
		self.assertEqual(headers["X-PharmacyOS-Signature"], expected)
		payload = json.loads(body)
		self.assertEqual(payload["event"], "availability.changed")
		self.assertEqual(payload["data"]["branch_code"], CODE)
		self.assertEqual(frappe.db.get_value("PharmacyOS Sync Event", {"dedupe_key": key}, "status"), "Sent")

	def test_failed_delivery_is_retried_then_marked(self):
		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "s", "outbound_secret")
		outbox.queue_event("catalog.changed", "Item", self.item.name, f"catalog:{self.item.name}:retry")
		with patch("requests.post", side_effect=ConnectionError("unreachable")):
			outbox.process_outbox()
		ev = frappe.get_doc("PharmacyOS Sync Event", {"dedupe_key": f"catalog:{self.item.name}:retry"})
		self.assertEqual(ev.status, "Failed")
		self.assertEqual(ev.attempts, 1)
		self.assertIn("unreachable", ev.last_error)
