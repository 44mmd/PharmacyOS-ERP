# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Stage C: receiving rules, branches (dimension, tagging, isolation) and the dashboard."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from pharmacyos_erp.pharmacy.branches import (
	assign_user_to_branch,
	branch_for_warehouse,
	get_user_branches,
	resolve_warehouses,
)
from pharmacyos_erp.pharmacy.dashboard import get_dashboard
from pharmacyos_erp.pharmacy.receiving import create_receiving_batch
from pharmacyos_erp.tests.utils import (
	COMPANY,
	WAREHOUSE,
	WAREHOUSE_2,
	make_batch,
	make_medicine,
	make_sales_invoice,
	make_user,
	receive,
	set_settings,
)

SUPPLIER = "_Test Supplier"


def make_receipt(item_code, batch, qty=10, submit=False):
	pr = frappe.get_doc(
		{
			"doctype": "Purchase Receipt",
			"company": COMPANY,
			"supplier": SUPPLIER,
			"currency": "INR",
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"rate": 100,
					"warehouse": WAREHOUSE,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
				}
			],
		}
	)
	pr.insert()
	if submit:
		pr.submit()
	return pr


def ensure_branch(name, warehouse):
	if not frappe.db.exists("Branch", name):
		frappe.get_doc({"doctype": "Branch", "branch": name, "pharmacyos_warehouse": warehouse}).insert()
	return name


class TestReceiving(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_settings(
			require_expiry_on_receipt=1,
			block_expired_receipt=1,
			min_shelf_life_on_receipt=90,
			short_shelf_life_action="Warn",
		)
		cls.item = make_medicine("POS-TEST-RECV")

	def test_expired_batch_cannot_be_received(self):
		batch = make_batch(self.item.name, "R-EXPIRED", -1)
		with self.assertRaises(frappe.ValidationError):
			make_receipt(self.item.name, batch)

	def test_short_shelf_life_warns_or_blocks(self):
		batch = make_batch(self.item.name, "R-SHORT", 30)
		frappe.clear_messages()
		make_receipt(self.item.name, batch)
		self.assertTrue(any("R-SHORT" in str(m) for m in frappe.get_message_log()))
		set_settings(short_shelf_life_action="Block")
		try:
			with self.assertRaises(frappe.ValidationError):
				make_receipt(self.item.name, batch)
		finally:
			set_settings(short_shelf_life_action="Warn")

	def test_missing_expiry_is_rejected(self):
		# ERPNext itself refuses an expiry-less batch for an expiry-tracked item; PharmacyOS checks
		# again on receipt (defence in depth), exercised here by clearing the date directly.
		batch = make_batch(self.item.name, "R-NOEXP", 300)
		frappe.db.set_value("Batch", batch, "expiry_date", None)
		with self.assertRaises(frappe.ValidationError):
			make_receipt(self.item.name, batch)

	def test_healthy_batch_receives_and_posts_stock(self):
		batch = make_batch(self.item.name, "R-GOOD", 500)
		pr = make_receipt(self.item.name, batch, qty=7, submit=True)
		self.assertEqual(pr.docstatus, 1)
		sle = frappe.get_all(
			"Stock Ledger Entry", filters={"voucher_no": pr.name, "is_cancelled": 0}, fields=["actual_qty"]
		)
		self.assertEqual(sum(s.actual_qty for s in sle), 7)

	def test_create_receiving_batch_reuses_and_guards_expiry(self):
		exp = add_days(nowdate(), 365)
		first = create_receiving_batch(self.item.name, "R-API-1", exp)
		again = create_receiving_batch(self.item.name, "R-API-1", exp)
		self.assertEqual(first["name"], again["name"])
		with self.assertRaises(frappe.ValidationError):
			create_receiving_batch(self.item.name, "R-API-1", add_days(exp, 10))  # same batch, other expiry

	def test_non_medicines_are_not_checked(self):
		item = frappe.get_doc("Item", self.item.name)
		item.db_set("pharma_is_medicine", 0)
		try:
			batch = make_batch(self.item.name, "R-SHORT-2", 10)
			frappe.clear_messages()
			make_receipt(self.item.name, batch)
			self.assertFalse(any("R-SHORT-2" in str(m) for m in frappe.get_message_log()))
		finally:
			item.db_set("pharma_is_medicine", 1)


class TestBranches(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.branch_a = ensure_branch("POS Test Branch A", WAREHOUSE)
		cls.branch_b = ensure_branch("POS Test Branch B", WAREHOUSE_2)

	def test_branch_for_warehouse(self):
		self.assertEqual(branch_for_warehouse(WAREHOUSE), self.branch_a)
		self.assertEqual(branch_for_warehouse(WAREHOUSE_2), self.branch_b)

	def test_branch_dimension_tags_documents(self):
		from pharmacyos_erp.pharmacy.branches import ensure_branch_dimension

		ensure_branch_dimension()
		if not frappe.get_meta("Sales Invoice").has_field("branch"):
			self.skipTest("Branch accounting dimension fields are created by a background job")
		item = make_medicine("POS-TEST-BRANCH")
		batch = make_batch(item.name, "BR-01", 400)
		receive(item.name, batch, 5)
		si = make_sales_invoice(item.name, batch, 1)
		self.assertEqual(si.branch, self.branch_a)

	def test_branch_isolation_via_user_permissions(self):
		user = make_user("pos-branch-a@example.test", ["Pharmacist", "Sales User", "Stock User"])
		assign_user_to_branch(user, self.branch_a)
		frappe.set_user(user)
		try:
			branches = [b.name for b in get_user_branches()]
			self.assertIn(self.branch_a, branches)
			self.assertNotIn(self.branch_b, branches)
			self.assertFalse(frappe.has_permission("Warehouse", "read", WAREHOUSE_2))
			with self.assertRaises(frappe.PermissionError):
				resolve_warehouses(branch=self.branch_b)
		finally:
			frappe.set_user("Administrator")

	def test_only_owner_can_assign_branches(self):
		user = make_user("pos-pharmacist@example.test", ["Pharmacist", "Sales User"])
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				assign_user_to_branch(user, self.branch_b)
		finally:
			frappe.set_user("Administrator")


class TestDashboard(IntegrationTestCase):
	def test_dashboard_sales_match_documents(self):
		item = make_medicine("POS-TEST-DASH")
		batch = make_batch(item.name, "D-01", 400)
		receive(item.name, batch, 20, rate=400)
		before = get_dashboard()["sales"]["total"]
		si = make_sales_invoice(item.name, batch, 2, submit=True)
		data = get_dashboard()
		self.assertAlmostEqual(data["sales"]["total"] - before, si.base_grand_total, places=2)
		self.assertIsNotNone(data["stock"])
		self.assertIn("counts", data["stock"])

	def test_cashier_sees_no_purchasing_or_stock_ledger(self):
		user = make_user("pos-dash-cashier@example.test", ["Cashier", "Sales User", "Accounts User"])
		frappe.set_user(user)
		try:
			data = get_dashboard()
			self.assertIsNone(data["purchasing"]["supplier_obligations"])
			self.assertIsNone(data["recent_movements"])
			self.assertIsNotNone(data["sales"])
		finally:
			frappe.set_user("Administrator")

	def test_dashboard_requires_pharmacy_role(self):
		user = make_user("pos-dash-none@example.test", ["Sales User"])
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_dashboard()
		finally:
			frappe.set_user("Administrator")
