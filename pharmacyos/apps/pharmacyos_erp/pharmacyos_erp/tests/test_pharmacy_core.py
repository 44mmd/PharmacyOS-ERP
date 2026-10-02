# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Stage B: medicines, expiry classification, batch views, FEFO, inventory health, reorder."""

import frappe
from erpnext.exceptions import BatchExpiredError
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from pharmacyos_erp.pharmacy.expiry import classify, get_batches, get_item_expiry_summary
from pharmacyos_erp.pharmacy.fefo import find_fefo_deviations, get_fefo_available
from pharmacyos_erp.pharmacy.inventory import get_inventory_health, get_reorder_suggestions
from pharmacyos_erp.tests.utils import (
	WAREHOUSE,
	make_batch,
	make_medicine,
	make_sales_invoice,
	make_user,
	receive,
	set_settings,
)


class TestExpiryClassification(IntegrationTestCase):
	def test_classify_windows(self):
		today = "2026-01-01"
		self.assertEqual(classify(None, today), ("none", None))
		self.assertEqual(classify("2025-12-31", today)[0], "expired")
		self.assertEqual(classify("2026-01-01", today), ("critical", 0))
		self.assertEqual(classify("2026-01-31", today, 30, 90), ("critical", 30))
		self.assertEqual(classify("2026-02-01", today, 30, 90), ("soon", 31))
		self.assertEqual(classify("2026-04-01", today, 30, 90), ("soon", 90))
		self.assertEqual(classify("2026-04-02", today, 30, 90), ("healthy", 91))


class TestPharmacyCore(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_settings(fefo_mode="Warn", critical_days=30, expiring_soon_days=90)
		cls.item = make_medicine(
			"POS-TEST-FEFO", item_name="Test FEFO Medicine", pharma_name_ar="دواء اختبار"
		)
		# expired (received while still valid), near, far
		cls.b_expired = make_batch(cls.item.name, "T-EXP-01", -10)
		cls.b_near = make_batch(cls.item.name, "T-NEAR-01", 20)
		cls.b_far = make_batch(cls.item.name, "T-FAR-01", 400)
		receive(cls.item.name, cls.b_expired, 5, posting_date=add_days(nowdate(), -40))
		receive(cls.item.name, cls.b_near, 10)
		receive(cls.item.name, cls.b_far, 30)

	def test_medicine_defaults(self):
		self.assertEqual(self.item.pharma_is_medicine, 1)
		self.assertEqual(self.item.has_batch_no, 1)
		self.assertEqual(self.item.has_expiry_date, 1)
		self.assertEqual(self.item.is_stock_item, 1)

	def test_batches_view_uses_batch_id_and_buckets(self):
		data = get_batches(bucket="all", search="POS-TEST-FEFO")
		ids = {r["batch_id"] for r in data["rows"]}
		self.assertEqual(ids, {"T-EXP-01", "T-NEAR-01", "T-FAR-01"})
		for r in data["rows"]:
			self.assertNotEqual(r["batch_id"], r["batch"])  # hash name is never the displayed number
		self.assertEqual(data["counts"]["expired"], 1)
		self.assertEqual(data["counts"]["30"], 1)
		statuses = {r["batch_id"]: r["status"] for r in data["rows"]}
		self.assertEqual(statuses, {"T-EXP-01": "expired", "T-NEAR-01": "critical", "T-FAR-01": "healthy"})
		expired_only = get_batches(bucket="expired", search="POS-TEST-FEFO")
		self.assertEqual([r["batch_id"] for r in expired_only["rows"]], ["T-EXP-01"])

	def test_value_at_risk_uses_valuation_rate(self):
		data = get_batches(bucket="expired", search="POS-TEST-FEFO")
		row = data["rows"][0]
		self.assertAlmostEqual(row["value"], row["qty"] * row["valuation_rate"], places=2)

	def test_erpnext_fefo_order_excludes_expired(self):
		order = [r["batch_no"] for r in get_fefo_available(self.item.name, WAREHOUSE)]
		self.assertEqual(order, [self.b_near, self.b_far])

	def test_expired_batch_sale_is_rejected_by_erpnext(self):
		with self.assertRaises(BatchExpiredError):
			make_sales_invoice(self.item.name, self.b_expired, 1)

	def test_fefo_warns_when_later_batch_chosen(self):
		set_settings(fefo_mode="Warn")
		frappe.clear_messages()
		si = make_sales_invoice(self.item.name, self.b_far, 1)
		self.assertTrue(any("T-NEAR-01" in str(m) for m in frappe.get_message_log()))
		self.assertEqual(len(find_fefo_deviations(si)), 1)

	def test_fefo_silent_for_earliest_batch(self):
		si = make_sales_invoice(self.item.name, self.b_near, 1)
		self.assertEqual(find_fefo_deviations(si), [])

	def test_fefo_block_mode(self):
		set_settings(fefo_mode="Block")
		try:
			with self.assertRaises(frappe.ValidationError):
				make_sales_invoice(self.item.name, self.b_far, 1)
		finally:
			set_settings(fefo_mode="Warn")

	def test_fefo_ignores_non_medicines(self):
		item = frappe.get_doc("Item", self.item.name)
		item.db_set("pharma_is_medicine", 0)
		try:
			si = make_sales_invoice(self.item.name, self.b_far, 1)
			self.assertEqual(find_fefo_deviations(si), [])
		finally:
			item.db_set("pharma_is_medicine", 1)

	def test_item_expiry_summary(self):
		summary = get_item_expiry_summary(self.item.name)
		self.assertEqual(summary["batches"], 3)
		self.assertEqual(summary["nearest"]["batch_id"], "T-NEAR-01")
		self.assertEqual(summary["status_counts"]["expired"], 1)

	def test_inventory_health_state(self):
		data = get_inventory_health(search="POS-TEST-FEFO", warehouse=WAREHOUSE)
		row = data["rows"][0]
		self.assertEqual(row["state"], "expired")  # holds expired stock
		self.assertEqual(row["qty"], 45)
		self.assertEqual(row["expired_qty"], 5)

	def test_reorder_suggestion_rule(self):
		item = make_medicine(
			"POS-TEST-REORDER", reorder_warehouse=WAREHOUSE, reorder_level=50, reorder_qty=20
		)
		batch = make_batch(item.name, "T-RO-01", 300)
		receive(item.name, batch, 10)
		rows = [
			r for r in get_reorder_suggestions(warehouse=WAREHOUSE)["rows"] if r["item_code"] == item.name
		]
		self.assertEqual(len(rows), 1)
		# max(reorder qty 20, shortfall 50 - 10) = 40
		self.assertEqual(rows[0]["suggested_qty"], 40)

	def test_views_require_pharmacy_role(self):
		user = make_user("pos-norole@example.test", ["Stock User"])
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_batches()
			with self.assertRaises(frappe.PermissionError):
				get_inventory_health()
		finally:
			frappe.set_user("Administrator")

	def test_create_medicine_requires_item_create_permission(self):
		from pharmacyos_erp.pharmacy.medicine import create_medicine

		user = make_user("pos-cashier@example.test", ["Cashier", "Sales User"])
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				create_medicine(item_name="Should Not Exist", item_group="Products", stock_uom="Nos")
		finally:
			frappe.set_user("Administrator")
