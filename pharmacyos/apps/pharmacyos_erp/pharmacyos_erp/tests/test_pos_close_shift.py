# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Closing the shift at the counter (/pos): the cashier counts the drawer, the server records ERPNext's
POS Closing Entry with ERPNext's expected amounts — as the cashier, for their own shift only, once."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from pharmacyos_erp.pos import api
from pharmacyos_erp.tests.test_pos_shift import ensure_pos_profile
from pharmacyos_erp.tests.test_web_pos import CASHIER_ROLES, ensure_price, open_shift, rid
from pharmacyos_erp.tests.utils import make_batch, make_medicine, make_user, receive

CASHIER = "close-shift-cashier@example.com"
COUNTER = "PharmacyOS Close Shift Counter"


class TestPOSCloseShift(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(CASHIER, CASHIER_ROLES)
		ensure_pos_profile(COUNTER, [CASHIER])
		cls.item = make_medicine("CLOSE-SHIFT-MED", item_name="Close Shift Medicine")
		ensure_price(cls.item.name, 1500)
		receive(cls.item.name, make_batch(cls.item.name, "CS-BATCH", 300), 10)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_cashier_counts_the_drawer_and_closes_their_own_shift_once(self):
		open_shift(CASHIER, COUNTER)
		frappe.set_user(CASHIER)
		sale = api.checkout(
			pos_profile=COUNTER,
			items=[{"item_code": self.item.name, "qty": 2}],
			payments=[{"mode_of_payment": "Cash", "amount": 3000}],
			request_id=rid(),
		)

		summary = api.shift_summary()
		cash = next(p for p in summary["payments"] if p["mode_of_payment"] == "Cash")
		self.assertEqual(flt(cash["expected_amount"]), 3000)  # opening 0 + the cash sale
		self.assertGreaterEqual(summary["sales"], 1)
		self.assertIsNone(summary["name"], "a summary saves nothing")

		with self.assertRaises(frappe.ValidationError):
			api.close_shift(counted=[])  # every payment method must be counted

		closed = api.close_shift(counted=[{"mode_of_payment": "Cash", "closing_amount": 2900}])
		closing = frappe.get_doc("POS Closing Entry", closed["name"])
		self.assertEqual((closing.docstatus, closing.owner, closing.user), (1, CASHIER, CASHIER))
		row = next(p for p in closing.payment_reconciliation if p.mode_of_payment == "Cash")
		self.assertEqual((flt(row.expected_amount), flt(row.closing_amount), flt(row.difference)), (3000, 2900, -100))
		self.assertIn(sale["name"], [r.sales_invoice for r in closing.get("sales_invoices") or []])
		self.assertEqual(frappe.db.get_value("POS Opening Entry", closing.pos_opening_entry, "status"), "Closed")

		# a repeated click finds no open shift: the shift is never closed twice
		with self.assertRaises(frappe.ValidationError):
			api.close_shift(counted=[{"mode_of_payment": "Cash", "closing_amount": 2900}])
		self.assertIsNone(api.get_context()["shift"])

	def test_no_shift_no_closing(self):
		make_user("close-shift-idle@example.com", CASHIER_ROLES)
		frappe.set_user("close-shift-idle@example.com")
		with self.assertRaises(frappe.ValidationError):
			api.shift_summary()


class TestPOSCloseShiftOpeningFloat(IntegrationTestCase):
	def test_expected_cash_includes_the_opening_float(self):
		user = "close-shift-float@example.com"
		counter = "PharmacyOS Close Shift Float Counter"
		make_user(user, CASHIER_ROLES)
		ensure_pos_profile(counter, [user])
		item = make_medicine("CLOSE-FLOAT-MED", item_name="Close Float Medicine")
		ensure_price(item.name, 2000)
		receive(item.name, make_batch(item.name, "CF-BATCH", 300), 10)
		frappe.set_user(user)
		from erpnext.selling.page.point_of_sale.point_of_sale import create_opening_voucher

		create_opening_voucher(counter, frappe.get_cached_value("POS Profile", counter, "company"), [{"mode_of_payment": "Cash", "opening_amount": 25000}])
		api.checkout(pos_profile=counter, items=[{"item_code": item.name, "qty": 1}], payments=[{"mode_of_payment": "Cash", "amount": 5000}], request_id=rid())
		cash = next(p for p in api.shift_summary()["payments"] if p["mode_of_payment"] == "Cash")
		# 25,000 float + 2,000 sale (5,000 tendered, 3,000 change given back)
		self.assertEqual((flt(cash["opening_amount"]), flt(cash["expected_amount"])), (25000, 27000))
		closed = api.close_shift(counted=[{"mode_of_payment": "Cash", "closing_amount": 27000}])
		self.assertEqual(next(p for p in closed["payments"] if p["mode_of_payment"] == "Cash")["difference"], 0)
		frappe.set_user("Administrator")
