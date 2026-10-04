# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Counter staff issue counter returns, not arbitrary credit notes (round-3 red-team).

Reproduced before the fix: a Cashier (and a Pharmacist) created a free-standing credit note for a
non-medicine item — with or without stock — and a money-only credit note against a stock sale
(refund, goods kept). Now those need a pharmacy manager or the accountant (CreditNoteAuthorityError,
403); the linked counter return keeps working, within the sold quantities and charged rates.
The permission matrix runs the two refusals over HTTP as every profile (`credit_note.*`).
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from pharmacyos_erp.pharmacy.returns import CreditNoteAuthorityError
from pharmacyos_erp.tests.test_remediation import ensure_cash_mode, sale_doc
from pharmacyos_erp.tests.utils import (
	COMPANY,
	CUSTOMER,
	WAREHOUSE,
	make_batch,
	make_medicine,
	make_user,
	profile_roles,
	receive,
)

CASH = "Cash - _TC"


def doc_for(item_code, qty, batch=None, return_against=None, update_stock=1, docstatus=1, customer=CUSTOMER):
	"""A sale (qty > 0) or a return / credit note (qty < 0, or `return_against` set): qty used as given."""
	doc = sale_doc(
		item_code, abs(qty), batch, is_return=qty < 0 or bool(return_against), return_against=return_against
	)
	doc["items"][0]["qty"] = qty
	doc["payments"][0].update({"account": CASH, "amount": qty * doc["items"][0]["rate"]})
	doc["update_stock"] = update_stock
	doc["customer"] = customer
	if not batch:
		for row in doc["items"]:
			row.pop("batch_no", None)
			row.pop("use_serial_batch_fields", None)
	return {**doc, "docstatus": docstatus}


class TestCreditNoteAuthority(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()
		# fixed accounts, re-used across runs (Frappe throttles bulk user creation)
		cls.cashier = make_user("cn-cashier@example.com", profile_roles("Cashier"))
		cls.other_cashier = make_user("cn-cashier2@example.com", profile_roles("Cashier"))
		cls.pharmacist = make_user("cn-pharmacist@example.com", profile_roles("Pharmacist"))
		cls.manager = make_user("cn-manager@example.com", profile_roles("Pharmacy Manager"))
		for user in (cls.cashier, cls.other_cashier, cls.pharmacist, cls.manager):
			frappe.defaults.set_user_default("Company", COMPANY, user)

	def setUp(self):
		tag = frappe.generate_hash(length=6).upper()
		self.product = (
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": f"CN-PROD-{tag}",
					"item_name": f"Product {tag}",
					"item_group": "Products",
					"stock_uom": "Nos",
					"is_stock_item": 1,
				}
			)
			.insert()
			.name
		)
		receive_plain(self.product, 20)
		self.medicine = make_medicine(f"CN-MED-{tag}").name
		self.batch = make_batch(self.medicine, f"CNB-{tag}", 300)
		receive(self.medicine, self.batch, 20)

	def as_user(self, user, doc):
		frappe.set_user(user)
		try:
			return frappe.get_doc(doc).insert()
		finally:
			frappe.set_user("Administrator")

	def sale(self, code=None, qty=2, batch=None, user="Administrator", update_stock=1):
		code = code or self.product
		return self.as_user(user, doc_for(code, qty, batch, update_stock=update_stock)).name

	# ------------------------------------------------------------------ refused for counter staff

	def test_standalone_credit_note_is_refused_for_counter_staff(self):
		for user in (self.cashier, self.pharmacist):
			for update_stock in (1, 0):
				with self.assertRaises(CreditNoteAuthorityError):
					self.as_user(user, doc_for(self.product, -1, update_stock=update_stock))
			with self.assertRaises(CreditNoteAuthorityError):  # a draft too: refused before it exists
				self.as_user(user, doc_for(self.product, -1, docstatus=0))
			with self.assertRaises(CreditNoteAuthorityError):
				self.as_user(user, doc_for(self.medicine, -1, self.batch))

	def test_money_only_credit_note_against_a_stock_sale_is_refused(self):
		sale = self.sale()
		for user in (self.cashier, self.pharmacist):
			with self.assertRaises(CreditNoteAuthorityError):
				self.as_user(user, doc_for(self.product, -1, return_against=sale, update_stock=0))

	def test_managers_keep_the_authority(self):
		self.as_user(self.manager, doc_for(self.product, -1))
		sale = self.sale()
		self.as_user(self.manager, doc_for(self.product, -1, return_against=sale, update_stock=0))

	# ------------------------------------------------------------------ the counter return still works

	def test_linked_counter_returns_work(self):
		medicine_sale = self.sale(self.medicine, 2, self.batch)
		product_sale = self.sale(self.product, 2)
		for user in (self.cashier, self.pharmacist):
			ret = self.as_user(user, doc_for(self.medicine, -1, self.batch, return_against=medicine_sale))
			self.assertEqual(ret.docstatus, 1)
			ret = self.as_user(user, doc_for(self.product, -1, return_against=product_sale))
			self.assertEqual(ret.docstatus, 1)

	def test_return_against_another_cashiers_sale_works(self):
		sale = self.sale(user=self.other_cashier)
		ret = self.as_user(self.cashier, doc_for(self.product, -1, return_against=sale))
		self.assertEqual((ret.docstatus, flt(ret.items[0].qty)), (1, -1))

	def test_return_against_a_sale_the_user_cannot_open_is_refused(self):
		"""Restricted to one customer (User Permission): no returns against other customers' sales."""
		other = (
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": f"CN Other {frappe.generate_hash(length=4)}",
					"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}),
				}
			)
			.insert()
			.name
		)
		sale = self.as_user("Administrator", doc_for(self.product, 2, customer=other)).name
		restricted = make_user("cn-restricted@example.com", profile_roles("Cashier"))
		frappe.db.delete("User Permission", {"user": restricted})
		frappe.defaults.set_user_default("Company", COMPANY, restricted)
		frappe.get_doc(
			{"doctype": "User Permission", "user": restricted, "allow": "Customer", "for_value": CUSTOMER}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			self.as_user(restricted, doc_for(self.product, -1, return_against=sale, customer=other))

	# ------------------------------------------------------------------ manipulated returns

	def test_manipulated_returns_are_refused(self):
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		sale = self.sale(self.medicine, 2, self.batch)
		product_sale = self.sale(self.product, 2)
		for user in (self.cashier, self.pharmacist):
			with self.assertRaises(StockOverReturnError):  # more than was sold
				self.as_user(user, doc_for(self.medicine, -3, self.batch, return_against=sale))
			with self.assertRaises(StockOverReturnError):  # an item that was not on the sale
				self.as_user(user, doc_for(self.product, -1, return_against=sale))
			bad_rate = doc_for(self.product, -1, return_against=product_sale)
			bad_rate["items"][0]["rate"] = 1500  # refund above the rate charged
			with self.assertRaises(StockOverReturnError):
				self.as_user(user, bad_rate)
		ret = self.as_user(self.cashier, doc_for(self.product, -1, return_against=product_sale)).name
		with self.assertRaises(frappe.ValidationError):  # a "return" of a return
			self.as_user(self.cashier, doc_for(self.product, 1, return_against=ret))
		with self.assertRaises(frappe.ValidationError):  # someone else's sale under another customer
			self.as_user(
				self.cashier,
				doc_for(
					self.product,
					-1,
					return_against=product_sale,
					customer=frappe.db.get_value("Customer", {"name": ["!=", CUSTOMER]}),
				),
			)

	def test_pos_invoice_standalone_return_is_refused(self):
		pos = {
			"doctype": "POS Invoice",
			"company": COMPANY,
			"customer": CUSTOMER,
			"is_pos": 1,
			"update_stock": 1,
			"is_return": 1,
			"items": [{"item_code": self.product, "qty": -1, "rate": 1000, "warehouse": WAREHOUSE}],
			"payments": [{"mode_of_payment": "Cash", "account": CASH, "amount": -1000}],
		}
		from pharmacyos_erp.pharmacy.returns import validate_return

		# (ERPNext first insists on an open POS shift; the authority rule itself, as the cashier:)
		frappe.set_user(self.cashier)
		try:
			with self.assertRaises(CreditNoteAuthorityError):
				validate_return(frappe.get_doc(pos))
		finally:
			frappe.set_user("Administrator")


def receive_plain(item_code, qty):
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": COMPANY,
			"items": [{"item_code": item_code, "qty": qty, "t_warehouse": WAREHOUSE, "basic_rate": 100}],
		}
	)
	se.insert()
	se.submit()
