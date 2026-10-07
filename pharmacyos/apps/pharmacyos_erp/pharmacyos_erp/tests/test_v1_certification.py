# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""LOCAL ERP v1 certification fixes (QA pass, October 2026), each proven on the server:

* counter staff sell at the pharmacy's prices — also through the document API, not only the POS screen
  (`pharmacy/counter_pricing.py`): no invented price, no discount where the counter allows none, and no
  discount above PharmacyOS Settings → Maximum Counter Discount; managers are not limited;
* the Purchasing Officer receives medicines (creates the supplier's batch on receipt);
* the owner keeps the staff directory (Employee records), which ERPNext reserves for HR roles.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from pharmacyos_erp.pharmacy.counter_pricing import CounterPriceError
from pharmacyos_erp.pos import api
from pharmacyos_erp.tests.test_pos_shift import ensure_pos_profile
from pharmacyos_erp.tests.test_web_pos import CASHIER_ROLES, ensure_price, open_shift, rid
from pharmacyos_erp.tests.utils import COMPANY, WAREHOUSE, make_batch, make_medicine, make_user, profile_roles, receive

CASHIER = "cert-cashier@example.com"
MANAGER = "cert-manager@example.com"
BUYER = "cert-buyer@example.com"
OWNER = "cert-owner@example.com"
COUNTER = "PharmacyOS Cert Counter"  # allows discounts
MANAGER_COUNTER = "PharmacyOS Cert Manager Counter"
STRICT_COUNTER = "PharmacyOS Cert Strict Counter"  # no discounts, no price changes


class TestCounterPricing(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(CASHIER, CASHIER_ROLES)
		make_user(MANAGER, profile_roles("Pharmacy Manager"))
		ensure_pos_profile(COUNTER, [CASHIER])
		ensure_pos_profile(MANAGER_COUNTER, [MANAGER])
		ensure_pos_profile(STRICT_COUNTER, [CASHIER])
		for name in (COUNTER, MANAGER_COUNTER):
			frappe.db.set_value("POS Profile", name, "allow_discount_change", 1)
		frappe.db.set_value("POS Profile", STRICT_COUNTER, {"allow_discount_change": 0, "allow_rate_change": 0})
		frappe.db.set_single_value("PharmacyOS Settings", "max_counter_discount", 10)
		cls.item = make_medicine("CERT-MED", item_name="Cert Medicine")
		ensure_price(cls.item.name, 1000)
		cls.batch = make_batch(cls.item.name, "CERT-B1", 400)
		receive(cls.item.name, cls.batch, 200)
		open_shift(CASHIER, COUNTER)
		open_shift(MANAGER, MANAGER_COUNTER)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")

	def invoice(self, profile=COUNTER, rate=None, discount=None, sale_discount=None, is_pos=1):
		row = {"item_code": self.item.name, "qty": 1, "warehouse": WAREHOUSE, "batch_no": self.batch, "use_serial_batch_fields": 1}
		if rate is not None:
			row.update({"rate": rate, "price_list_rate": rate})
		if discount is not None:
			row["discount_percentage"] = discount
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": COMPANY,
				"customer": frappe.db.get_value("POS Profile", profile, "customer"),
				"is_pos": is_pos,
				"pos_profile": profile if is_pos else None,
				"update_stock": 1,
				"set_warehouse": WAREHOUSE,
				"selling_price_list": "Standard Selling",
				"items": [row],
				"additional_discount_percentage": sale_discount or 0,
			}
		)
		if is_pos:
			doc.append("payments", {"mode_of_payment": "Cash", "amount": 1000})
		return doc

	def test_an_invented_price_is_refused_through_the_document_api(self):
		frappe.set_user(CASHIER)
		with self.assertRaises(CounterPriceError):
			self.invoice(rate=1).insert()  # the 1-dinar sale QA found
		with self.assertRaises(CounterPriceError):
			self.invoice(rate=1, is_pos=0).insert()  # a plain (credit) invoice too
		with self.assertRaises(CounterPriceError):
			self.invoice(profile=STRICT_COUNTER, discount=5).insert()  # no discounts on this counter

	def test_a_full_discount_is_refused(self):
		frappe.set_user(CASHIER)
		with self.assertRaises(CounterPriceError):
			self.invoice(sale_discount=100).insert()
		with self.assertRaises(CounterPriceError):
			self.invoice(discount=100).insert()

	def test_the_counter_discount_ceiling(self):
		frappe.set_user(CASHIER)
		self.assertEqual(api.get_context()["max_discount"], 10)
		ok = api.checkout(pos_profile=COUNTER, items=[{"item_code": self.item.name, "qty": 1, "discount_percentage": 10}], payments=[{"mode_of_payment": "Cash", "amount": 900}], request_id=rid())
		self.assertEqual(ok["grand_total"], 900)
		with self.assertRaises(CounterPriceError):  # shown before payment already
			api.quote(pos_profile=COUNTER, items=[{"item_code": self.item.name, "qty": 1, "discount_percentage": 15}])
		with self.assertRaises(CounterPriceError):
			api.checkout(pos_profile=COUNTER, items=[{"item_code": self.item.name, "qty": 1, "discount_percentage": 15}], payments=[{"mode_of_payment": "Cash", "amount": 850}], request_id=rid())
		with self.assertRaises(CounterPriceError):  # 10 % per line and 10 % on the sale = 19 % of the list price
			api.checkout(pos_profile=COUNTER, items=[{"item_code": self.item.name, "qty": 1, "discount_percentage": 10}], payments=[{"mode_of_payment": "Cash", "amount": 810}], request_id=rid(), additional_discount_percentage=10)
		# a correct sale through the document API passes the guard
		doc = self.invoice(discount=5)
		doc.insert()
		self.assertEqual(doc.grand_total, 950)

	def test_managers_are_not_limited(self):
		frappe.set_user(MANAGER)
		self.assertEqual(api.get_context()["max_discount"], 100)
		sale = api.checkout(pos_profile=MANAGER_COUNTER, items=[{"item_code": self.item.name, "qty": 1, "discount_percentage": 50}], payments=[{"mode_of_payment": "Cash", "amount": 500}], request_id=rid())
		self.assertEqual(sale["grand_total"], 500)


class TestStaffAndReceivingRights(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(BUYER, profile_roles("Purchasing Officer"))
		make_user(OWNER, profile_roles("Pharmacy Owner"))
		cls.item = make_medicine("CERT-RECV", item_name="Cert Receiving Medicine")
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_purchasing_officer_receives_a_medicine_with_its_batch(self):
		from pharmacyos_erp.pharmacy.receiving import create_receiving_batch

		supplier = frappe.db.get_value("Supplier", {}, "name") or frappe.get_doc({"doctype": "Supplier", "supplier_name": "Cert Supplier", "supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0})}).insert(ignore_permissions=True).name
		frappe.set_user(BUYER)
		batch = create_receiving_batch(self.item.name, "CERT-RCV-01", add_days(nowdate(), 500))
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"company": COMPANY,
				"supplier": supplier,
				"set_warehouse": WAREHOUSE,
				"items": [{"item_code": self.item.name, "qty": 5, "rate": 300, "warehouse": WAREHOUSE, "batch_no": batch["name"], "use_serial_batch_fields": 1}],
			}
		)
		receipt.insert()
		receipt.submit()
		self.assertEqual(receipt.docstatus, 1)
		# buyers record batches; correcting a recorded batch stays with stock managers
		doc = frappe.get_doc("Batch", batch["name"])
		doc.expiry_date = add_days(nowdate(), 100)
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_owner_keeps_the_staff_directory(self):
		frappe.set_user(OWNER)
		employee = frappe.get_doc(
			{"doctype": "Employee", "first_name": "Cert Staff", "gender": "Female", "date_of_birth": "1995-05-05", "date_of_joining": nowdate(), "company": COMPANY, "status": "Active"}
		).insert()
		employee.status = "Left"
		employee.relieving_date = nowdate()
		employee.save()
		with self.assertRaises(frappe.PermissionError):  # history stays: no deleting staff records
			frappe.delete_doc("Employee", employee.name)
