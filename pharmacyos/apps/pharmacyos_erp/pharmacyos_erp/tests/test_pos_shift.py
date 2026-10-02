# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Flow B with a real cashier: open own shift → POS sale → close own shift with reconciled cash."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, now_datetime, nowdate

from pharmacyos_erp.tests.utils import (
	COMPANY,
	CUSTOMER,
	WAREHOUSE,
	make_batch,
	make_medicine,
	make_user,
	receive,
)

CASHIER_ROLES = ["Cashier", "Sales User", "Accounts User"]  # the Cashier role profile


def ensure_pos_profile(name, users):
	if frappe.db.exists("POS Profile", name):
		return name
	cash = frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0}, "name")
	mop = frappe.get_doc("Mode of Payment", "Cash")
	if not any(a.company == COMPANY for a in mop.accounts):
		mop.append("accounts", {"company": COMPANY, "default_account": cash})
		mop.save()
	frappe.get_doc(
		{
			"doctype": "POS Profile",
			"__newname": name,
			"company": COMPANY,
			"warehouse": WAREHOUSE,
			"currency": "INR",
			"customer": CUSTOMER,
			"selling_price_list": "Standard Selling",
			"write_off_account": "Write Off - _TC",
			"write_off_cost_center": "_Test Cost Center - _TC",
			"cost_center": "_Test Cost Center - _TC",
			"update_stock": 1,
			"payments": [{"mode_of_payment": "Cash", "default": 1}],
			"applicable_for_users": [{"user": u} for u in users],
		}
	).insert(ignore_permissions=True)
	return name


class TestCashierShift(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.cashier = make_user("pos-shift-cashier@example.com", CASHIER_ROLES)
		cls.other = make_user("pos-shift-other@example.com", CASHIER_ROLES)
		# ERPNext allows one open shift per POS profile (counter): one counter per cashier
		cls.profiles = {
			cls.cashier: ensure_pos_profile("PharmacyOS Test Counter 1", [cls.cashier]),
			cls.other: ensure_pos_profile("PharmacyOS Test Counter 2", [cls.other]),
		}
		cls.item = make_medicine("POS-SHIFT-MED", item_name="Shift Test Medicine")
		frappe.db.set_value("Item", cls.item.name, "standard_rate", 2500)
		cls.batch = make_batch(cls.item.name, "SH-0001", 300)
		receive(cls.item.name, cls.batch, 10)

	def tearDown(self):
		frappe.set_user("Administrator")

	def open_shift(self, user):
		frappe.set_user(user)
		opening = frappe.get_doc(
			{
				"doctype": "POS Opening Entry",
				"pos_profile": self.profiles[user],
				"company": COMPANY,
				"user": user,
				"period_start_date": now_datetime(),
				"posting_date": nowdate(),
				"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 10000}],
			}
		)
		opening.insert()
		opening.submit()
		return opening

	def test_cashier_runs_own_shift_end_to_end(self):
		opening = self.open_shift(self.cashier)
		self.assertEqual(opening.docstatus, 1)

		invoice = frappe.get_doc(
			{
				# ERPNext v17 POS runs in "Sales Invoice mode": counter sales are POS Sales Invoices
				"doctype": "Sales Invoice",
				"is_pos": 1,
				"is_created_using_pos": 1,  # as the POS screen creates it
				"pos_profile": self.profiles[self.cashier],
				"company": COMPANY,
				"customer": CUSTOMER,
				"currency": "INR",
				"debit_to": "Debtors - _TC",
				"update_stock": 1,
				"items": [
					{
						"item_code": self.item.name,
						"qty": 2,
						"rate": 2500,
						"warehouse": WAREHOUSE,
						"batch_no": self.batch,
						"use_serial_batch_fields": 1,
					}
				],
				"payments": [{"mode_of_payment": "Cash", "amount": 5000}],
			}
		)
		invoice.insert()
		invoice.submit()
		self.assertEqual(invoice.status, "Paid")

		from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
			make_closing_entry_from_opening,
		)

		closing = make_closing_entry_from_opening(opening)
		cash = next(p for p in closing.payment_reconciliation if p.mode_of_payment == "Cash")
		self.assertEqual(flt(cash.expected_amount), 5000)  # cash taken during this shift
		self.assertEqual([i.sales_invoice for i in closing.sales_invoices], [invoice.name])
		cash.closing_amount = 5000
		closing.insert()
		closing.submit()
		self.assertEqual(closing.docstatus, 1)

	def test_cashier_cannot_see_or_cancel_another_shift(self):
		opening = self.open_shift(self.other)
		frappe.set_user(self.cashier)
		self.assertFalse(frappe.has_permission("POS Opening Entry", "read", opening.name))
		self.assertFalse(frappe.has_permission("POS Opening Entry", "cancel", opening.name))
		frappe.set_user(self.other)
		self.assertFalse(frappe.has_permission("POS Opening Entry", "cancel", opening.name))
