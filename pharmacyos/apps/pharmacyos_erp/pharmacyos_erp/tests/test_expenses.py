# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Expenses are real, balanced, submitted ledger entries."""

import frappe
from frappe.tests import IntegrationTestCase

from pharmacyos_erp.pharmacy.expenses import record_expense
from pharmacyos_erp.tests.utils import COMPANY, make_user


class TestExpenses(IntegrationTestCase):
	def setUp(self):
		self.expense = frappe.db.get_value(
			"Account", {"company": COMPANY, "root_type": "Expense", "is_group": 0, "account_type": ""}, "name"
		) or frappe.db.get_value(
			"Account", {"company": COMPANY, "root_type": "Expense", "is_group": 0}, "name"
		)
		cash = frappe.db.get_value(
			"Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0}, "name"
		)
		mop = frappe.get_doc("Mode of Payment", "Cash")
		if not any(a.company == COMPANY for a in mop.accounts):
			mop.append("accounts", {"company": COMPANY, "default_account": cash})
			mop.save()

	def test_expense_posts_balanced_gl(self):
		name = record_expense(self.expense, 25000, "Cash", remark="Electricity")
		je = frappe.get_doc("Journal Entry", name)
		self.assertEqual(je.docstatus, 1)
		self.assertTrue(je.pharma_expense)
		gl = frappe.get_all(
			"GL Entry", filters={"voucher_no": name, "is_cancelled": 0}, fields=["account", "debit", "credit"]
		)
		self.assertEqual(sum(g.debit for g in gl), 25000)
		self.assertEqual(sum(g.credit for g in gl), 25000)
		self.assertIn(self.expense, [g.account for g in gl if g.debit])

	def test_rejects_bad_input(self):
		self.assertRaises(frappe.ValidationError, record_expense, self.expense, 0, "Cash")
		income = frappe.db.get_value(
			"Account", {"company": COMPANY, "root_type": "Income", "is_group": 0}, "name"
		)
		self.assertRaises(frappe.ValidationError, record_expense, income, 100, "Cash")

	def test_cashier_cannot_post_expenses(self):
		frappe.set_user(make_user("pos-cashier-exp@example.com", ["Cashier"]))
		try:
			self.assertRaises(frappe.PermissionError, record_expense, self.expense, 100, "Cash")
		finally:
			frappe.set_user("Administrator")
