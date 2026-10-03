# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Website orders pulled from PharmacyOS Cloud (Flow E, ERP side) against a fake Cloud."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate
from frappe.utils.password import set_encrypted_password

from pharmacyos_erp.integration import cloud
from pharmacyos_erp.tests.test_integration import BRANCH, CODE, ensure_api_branch, published_medicine
from pharmacyos_erp.tests.utils import WAREHOUSE, make_batch, receive, set_settings


class FakeCloud:
	"""Records acks; serves a feed. `down=True` simulates no internet."""

	def __init__(self, orders, down=False):
		self.orders, self.down, self.acks = orders, down, []

	def __call__(self, method, path, payload=None):
		if self.down:
			raise ConnectionError("Network is unreachable")
		if path.startswith("/integrations/erp/orders/feed"):
			return {"orders": self.orders}
		if path.endswith("/ack"):
			self.acks.append(payload)
			return {"ok": True}
		return {"ok": True}


def entry(order_id, item, qty, status="received", version=1):
	return {
		"order_id": order_id,
		"version": version,
		"status": status,
		"branch_code": CODE,
		"items": [{"item_code": item, "qty": qty}],
		"customer": {"id": "07700000001", "name": "زبون الموقع", "phone": "07700000001"},
		"delivery_address": "كربلاء",
		"payment_method": "Cash",
	}


class TestCloudOrders(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_api_branch()
		# pull_orders commits, so order ids must be unique per run (earlier runs' orders persist)
		cls.run_id = frappe.generate_hash(length=6).upper()
		cls.item = published_medicine("POS-CLOUD-MED")
		cls.expired = make_batch(cls.item.name, "CL-EXP", -3)
		cls.good = make_batch(cls.item.name, "CL-GOOD", 400)
		receive(cls.item.name, cls.expired, 5, posting_date=add_days(nowdate(), -40))
		receive(cls.item.name, cls.good, 8)
		cash = frappe.db.get_value(
			"Account", {"company": "_Test Company", "account_type": "Cash", "is_group": 0}, "name"
		)
		mop = frappe.get_doc("Mode of Payment", "Cash")
		if not any(a.company == "_Test Company" for a in mop.accounts):
			mop.append("accounts", {"company": "_Test Company", "default_account": cash})
			mop.save()

	def setUp(self):
		set_settings(
			enable_outbound_events=1,
			cloud_base_url="https://cloud.pharmacyos.invalid",
			online_payment_mode="Cash",
		)
		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "s" * 40, "outbound_secret")

	def tearDown(self):
		set_settings(enable_outbound_events=0, cloud_base_url=None)

	def pull(self, fake):
		with patch.object(cloud, "cloud_request", side_effect=fake):
			return cloud.pull_orders()

	def so(self, order_id):
		return frappe.get_doc("Sales Order", {"pharmacyos_order_id": order_id})

	def test_order_is_imported_once_and_reserves_stock(self):
		fake = FakeCloud([entry(f"HPH-T1-{self.run_id}", self.item.name, 2)])
		self.assertEqual(self.pull(fake)["applied"], 1)
		self.pull(fake)  # same version again (e.g. lost ack): no second Sales Order
		self.assertEqual(frappe.db.count("Sales Order", {"pharmacyos_order_id": f"HPH-T1-{self.run_id}"}), 1)
		so = self.so(f"HPH-T1-{self.run_id}")
		self.assertEqual(so.docstatus, 1)
		self.assertEqual(fake.acks[0], {"version": 1, "erp_ref": so.name, "erp_status": so.status})

	def test_completed_order_is_invoiced_from_non_expired_batches_only(self):
		fake = FakeCloud([entry(f"HPH-T2-{self.run_id}", self.item.name, 6)])
		self.pull(fake)
		fake.orders = [entry(f"HPH-T2-{self.run_id}", self.item.name, 6, status="completed", version=3)]
		self.pull(fake)
		self.pull(fake)  # replay: no second invoice
		so = self.so(f"HPH-T2-{self.run_id}")
		self.assertEqual(flt(so.per_delivered), 100, fake.acks)
		invoices = frappe.get_all(
			"Sales Invoice Item",
			filters={"sales_order": so.name, "docstatus": 1},
			pluck="parent",
			distinct=True,
		)
		self.assertEqual(len(invoices), 1)
		si = frappe.get_doc("Sales Invoice", invoices[0])
		self.assertEqual(si.status, "Paid")
		batches = {
			e.batch_no
			for row in si.items
			for e in frappe.get_all(
				"Serial and Batch Entry", filters={"parent": row.serial_and_batch_bundle}, fields=["batch_no"]
			)
		} | {row.batch_no for row in si.items if row.batch_no}
		self.assertNotIn(self.expired, batches)
		gl = frappe.get_all(
			"GL Entry", filters={"voucher_no": si.name, "is_cancelled": 0}, fields=["debit", "credit"]
		)
		self.assertAlmostEqual(sum(g.debit for g in gl), sum(g.credit for g in gl))

	def test_cancel_releases_the_reservation(self):
		fake = FakeCloud([entry(f"HPH-T3-{self.run_id}", self.item.name, 1)])
		self.pull(fake)
		fake.orders = [entry(f"HPH-T3-{self.run_id}", self.item.name, 1, status="cancelled", version=2)]
		self.pull(fake)
		self.assertEqual(self.so(f"HPH-T3-{self.run_id}").docstatus, 2)
		self.assertEqual(fake.acks[-1]["erp_status"], "Cancelled")

	def test_unsellable_order_is_acknowledged_with_an_error(self):
		fake = FakeCloud([entry(f"HPH-T4-{self.run_id}", self.item.name, 500)])  # far more than sellable stock
		result = self.pull(fake)
		self.assertEqual(result["rejected"], 1)
		self.assertIn("error", fake.acks[0])
		self.assertFalse(frappe.db.exists("Sales Order", {"pharmacyos_order_id": f"HPH-T4-{self.run_id}"}))

	def test_offline_pull_is_retried_and_recorded(self):
		result = self.pull(FakeCloud([], down=True))
		self.assertIn("error", result)
		self.assertIn("unreachable", frappe.db.get_single_value("PharmacyOS Settings", "cloud_last_error"))
		self.pull(FakeCloud([]))
		self.assertFalse(frappe.db.get_single_value("PharmacyOS Settings", "cloud_last_error"))

	def test_signature_matches_the_cloud_scheme(self):
		sig = cloud.sign_request("k" * 40, "1700000000", "GET", "/integrations/erp/orders/feed?limit=50", b"")
		import hashlib
		import hmac

		msg = f"1700000000\nGET\n/integrations/erp/orders/feed?limit=50\n{hashlib.sha256(b'').hexdigest()}".encode()
		self.assertEqual(sig, "sha256=" + hmac.new(b"k" * 40, msg, hashlib.sha256).hexdigest())

	def test_catalog_event_carries_public_fields_only(self):
		from pharmacyos_erp.integration.outbox import build_payload

		event = frappe._dict(
			event_type="catalog.changed",
			name="E1",
			creation=nowdate(),
			reference_name=self.item.name,
			dedupe_key="x",
		)
		item = build_payload(event)["data"]["item"]
		self.assertEqual(item["item_code"], self.item.name)
		for secret_field in ("valuation_rate", "buying_rate", "last_purchase_rate"):
			self.assertNotIn(secret_field, item)
