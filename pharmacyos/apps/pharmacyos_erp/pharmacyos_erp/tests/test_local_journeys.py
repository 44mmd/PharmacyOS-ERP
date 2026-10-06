# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""The pharmacy's core journey on the local ERP, end to end, through the same calls the counter makes:

supplier delivery (Purchase Receipt, batch + expiry) → stock in the warehouse → counter sale at /pos
(FEFO batch, stock out, ledger) → return at the counter → stock back into the same batch.
Nothing here needs the internet or the PharmacyOS Cloud.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from pharmacyos_erp.pos import api
from pharmacyos_erp.tests.test_operations import make_receipt
from pharmacyos_erp.tests.test_pos_shift import ensure_pos_profile
from pharmacyos_erp.tests.test_web_pos import CASHIER_ROLES, bin_qty, bundle_batches, ensure_price, open_shift, rid
from pharmacyos_erp.tests.utils import make_batch, make_medicine, make_user

CASHIER = "journey-cashier@example.com"
COUNTER = "PharmacyOS Journey Counter"


class TestLocalJourneys(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(CASHIER, CASHIER_ROLES)
		ensure_pos_profile(COUNTER, [CASHIER])
		open_shift(CASHIER, COUNTER)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_receive_from_supplier_sell_at_counter_return_restores_stock(self):
		item = make_medicine("JOURNEY-MED", item_name="Journey Medicine")
		ensure_price(item.name, 2000)
		start = bin_qty(item.name)

		# 1. the supplier's delivery is received with its printed batch and expiry
		batch = make_batch(item.name, "JRN-001", 400)
		receipt = make_receipt(item.name, batch, qty=12, submit=True)
		self.assertEqual(receipt.docstatus, 1)
		self.assertEqual(bin_qty(item.name), start + 12)

		# 2. the cashier sells 5 at the counter: priced by the ERP, stock and ledger move, batch recorded
		frappe.set_user(CASHIER)
		sale = api.checkout(
			pos_profile=COUNTER,
			items=[{"item_code": item.name, "qty": 5}],
			payments=[{"mode_of_payment": "Cash", "amount": 10000}],
			request_id=rid(),
		)
		frappe.set_user("Administrator")
		invoice = frappe.get_doc("Sales Invoice", sale["name"])
		self.assertEqual((invoice.docstatus, invoice.status, flt(invoice.grand_total)), (1, "Paid", 10000))
		self.assertEqual(bin_qty(item.name), start + 7)
		self.assertEqual(bundle_batches(invoice.name), {batch: 5})
		sold = frappe.get_all("Stock Ledger Entry", filters={"voucher_no": invoice.name, "is_cancelled": 0}, pluck="actual_qty")
		self.assertEqual(sum(sold), -5)
		# the receipt the counter prints renders for this sale
		html = frappe.get_print("Sales Invoice", invoice.name, print_format="PharmacyOS Receipt")
		self.assertIn(invoice.name, html)

		# 3. two come back: refund and stock return to the same batch
		frappe.set_user(CASHIER)
		line = api.get_return_candidate(invoice.name)["lines"][0]
		ret = api.submit_return(invoice.name, [{"row": line["row"], "qty": 2}], request_id=rid())
		frappe.set_user("Administrator")
		self.assertEqual(flt(frappe.db.get_value("Sales Invoice", ret["name"], "grand_total")), -4000)
		self.assertEqual(bin_qty(item.name), start + 9)
		self.assertEqual(bundle_batches(ret["name"]), {batch: 2})
