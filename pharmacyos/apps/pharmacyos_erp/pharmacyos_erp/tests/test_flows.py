# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""End-to-end pharmacy flows through real ERPNext documents.

Flow B — paid counter sale: batch → sale with payment → stock reduced → ledger → receipt.
Flow D — return: original sale → return against it → stock and ledger reversed, history kept.
(Flow A receiving, Flow C expired-sale block: test_operations / test_pharmacy_core.)
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from pharmacyos_erp.tests.utils import COMPANY, CUSTOMER, WAREHOUSE, make_batch, make_medicine, receive


def stock(item_code, batch=None):
	if batch:
		from erpnext.stock.doctype.batch.batch import get_batch_qty

		return flt(get_batch_qty(batch, WAREHOUSE))
	return flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": WAREHOUSE}, "actual_qty"))


def gl(voucher):
	return frappe.get_all(
		"GL Entry",
		filters={"voucher_no": voucher, "is_cancelled": 0},
		fields=["account", "debit", "credit"],
	)


def counter_sale(item_code, batch, qty, rate, return_against=None):
	si = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": COMPANY,
			"customer": CUSTOMER,
			"currency": "INR",
			"debit_to": "Debtors - _TC",
			"is_pos": 1,
			"update_stock": 1,
			"is_return": 1 if return_against else 0,
			"return_against": return_against,
			"items": [
				{
					"item_code": item_code,
					"qty": -qty if return_against else qty,
					"rate": rate,
					"warehouse": WAREHOUSE,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
					"income_account": "Sales - _TC",
					"expense_account": "Cost of Goods Sold - _TC",
					"cost_center": "_Test Cost Center - _TC",
				}
			],
			"payments": [{"mode_of_payment": "Cash", "amount": (-1 if return_against else 1) * qty * rate}],
		}
	)
	si.insert()
	si.submit()
	return si


class TestPharmacyFlows(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cash = frappe.db.get_value(
			"Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0}, "name"
		)
		mop = frappe.get_doc("Mode of Payment", "Cash")
		if not any(a.company == COMPANY for a in mop.accounts):
			mop.append("accounts", {"company": COMPANY, "default_account": cash})
			mop.save()
		cls.item = make_medicine("POS-FLOW-MED", item_name="Flow Test Medicine")
		cls.batch = make_batch(cls.item.name, "FL-0001", 365)
		receive(cls.item.name, cls.batch, 20, rate=1000)

	def test_flow_b_paid_sale_reduces_stock_posts_ledger_and_prints(self):
		before = stock(self.item.name, self.batch)
		sale = counter_sale(self.item.name, self.batch, 3, 2500)
		self.assertEqual(sale.status, "Paid")
		self.assertEqual(stock(self.item.name, self.batch), before - 3)
		entries = gl(sale.name)
		self.assertAlmostEqual(sum(e.debit for e in entries), sum(e.credit for e in entries))
		self.assertTrue(any(e.account == "Sales - _TC" and e.credit == 7500 for e in entries))
		self.assertTrue(any(e.account == "Cash - _TC" and e.debit == 7500 for e in entries), entries)
		if frappe.get_cached_value("Company", COMPANY, "enable_perpetual_inventory"):
			self.assertTrue(any(e.account == "Cost of Goods Sold - _TC" and e.debit == 3000 for e in entries))
		receipt = frappe.get_print("Sales Invoice", sale.name, print_format="PharmacyOS Receipt")
		self.assertIn("FL-0001", receipt)  # batch printed on the receipt

	def test_flow_d_return_reverses_stock_and_ledger_keeping_history(self):
		sale = counter_sale(self.item.name, self.batch, 2, 2500)
		after_sale = stock(self.item.name, self.batch)
		ret = counter_sale(self.item.name, self.batch, 2, 2500, return_against=sale.name)
		self.assertEqual(stock(self.item.name, self.batch), after_sale + 2)
		sale_gl = {e.account: e.credit - e.debit for e in gl(sale.name)}
		ret_gl = {e.account: e.credit - e.debit for e in gl(ret.name)}
		self.assertAlmostEqual(ret_gl["Sales - _TC"], -sale_gl["Sales - _TC"])
		sale.reload()
		self.assertEqual(sale.docstatus, 1)  # the original sale is never deleted or edited
		self.assertEqual(ret.return_against, sale.name)
