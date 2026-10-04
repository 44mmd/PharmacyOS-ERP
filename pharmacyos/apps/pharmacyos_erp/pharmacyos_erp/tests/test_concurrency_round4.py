# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Round 4 (D-5): a sale never dies with a database deadlock because another document is cancelled.

Independent validation saw ordinary counter sales (and cancellations) end in HTTP 508
`QueryDeadlockError` while a return or sale of an *unrelated* medicine was being cancelled: ERPNext
marks the cancelled document's batch bundle rows with `UPDATE … WHERE voucher_type = … AND
voucher_no = …`, and without an index on those columns the statement scans and locks the batch
entries of every item — including the row the concurrent sale holds for its own batch. The composite
indexes of `pharmacy/locking.py` make the statement touch only the cancelled document's rows.

These tests run real requests in parallel threads, each on its own database connection, exactly like
`pharmacyos/dev/concurrency_check.py --only unrelated_cancel` does against gunicorn.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from pharmacyos_erp.pharmacy.locking import VOUCHER_INDEXES, ensure_voucher_indexes, voucher_indexes_present
from pharmacyos_erp.tests.test_remediation import ensure_cash_mode, sale_doc
from pharmacyos_erp.tests.test_return_concurrency import (
	admin_client,
	batch_qty,
	simultaneously,
	submitted_doc,
)
from pharmacyos_erp.tests.utils import make_batch, make_medicine, receive


def controlled(response):
	"""200, or a business error — never a 5xx (a deadlock answers 508)."""
	return response.status_code == 200 or response.status_code == 417


class TestVoucherIndexes(IntegrationTestCase):
	def test_indexes_exist_and_setup_is_idempotent(self):
		self.assertEqual(voucher_indexes_present(), {doctype: True for doctype in VOUCHER_INDEXES})
		self.assertEqual(ensure_voucher_indexes(), [])
		# the cancellation statement uses the index (no full scan of the batch entries)
		plan = frappe.db.sql(
			"explain update `tabSerial and Batch Entry` set is_cancelled=1 where voucher_type=%s and voucher_no=%s",
			("Sales Invoice", "ACC-SINV-0000-00000"),
			as_dict=True,
		)
		self.assertTrue(plan and plan[0].get("key"), plan)
		self.assertIn("voucher_type_voucher_no_index", plan[0].get("key") or "")

	def test_patch_is_idempotent(self):
		from pharmacyos_erp.patches.v1 import serial_batch_voucher_indexes

		serial_batch_voucher_indexes.execute()
		serial_batch_voucher_indexes.execute()
		self.assertEqual(voucher_indexes_present(), {doctype: True for doctype in VOUCHER_INDEXES})


class TestSaleVersusCancellation(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()
		frappe.db.commit()

	def medicine(self):
		tag = frappe.generate_hash(length=6).upper()
		item = make_medicine(f"R4-DL-{tag}")
		batch = make_batch(item.name, f"R4DL-{tag}", 300)
		receive(item.name, batch, 10)
		return item.name, batch

	def race(self, victim_of):
		"""A submitted sale and a draft of medicine X at the same instant as the cancellation of `victim_of`."""
		x, xb = self.medicine()
		y, yb = self.medicine()
		ysale = frappe.get_doc(submitted_doc(y, 2, yb)).insert()
		yret = frappe.get_doc(submitted_doc(y, 2, yb, return_against=ysale.name)).insert()
		xsale = frappe.get_doc(submitted_doc(x, 1, xb)).insert()
		xret = frappe.get_doc(submitted_doc(x, 1, xb, return_against=xsale.name)).insert()
		frappe.db.commit()
		victim = {"y_return": yret.name, "y_sale": ysale.name, "x_return": xret.name}[victim_of]
		if victim_of == "y_sale":
			yret.reload()
			yret.cancel()
			frappe.db.commit()
		c1, c2, c3 = admin_client(), admin_client(), admin_client()
		responses = simultaneously(
			[
				lambda: c1.post("/api/method/frappe.client.insert", json={"doc": submitted_doc(x, 1, xb)}),
				lambda: c2.post(
					"/api/method/frappe.client.cancel", json={"doctype": "Sales Invoice", "name": victim}
				),
				lambda: c3.post("/api/method/frappe.client.insert", json={"doc": sale_doc(x, 1, xb)}),
			]
		)
		frappe.db.rollback()
		for kind, r in zip(("sale", "cancel", "draft"), responses, strict=True):
			self.assertTrue(
				controlled(r), f"{victim_of}: {kind} got {r.status_code} {r.get_data(as_text=True)[:200]}"
			)
		self.assertEqual(responses[0].status_code, 200, "the sale must go through")
		self.assertEqual(responses[1].status_code, 200, "the cancellation must go through")
		# persisted state: X's stock follows its own documents only; the victim is cancelled
		expected = 10 - 1 + 1 - 1 - (1 if victim_of == "x_return" else 0)
		self.assertEqual(flt(batch_qty(xb)), expected)
		self.assertEqual(frappe.db.get_value("Sales Invoice", victim, "docstatus"), 2)
		for voucher in (xsale.name, ysale.name):
			gl = frappe.get_all(
				"GL Entry", filters={"voucher_no": voucher, "is_cancelled": 0}, fields=["debit", "credit"]
			)
			self.assertAlmostEqual(sum(flt(g.debit) for g in gl), sum(flt(g.credit) for g in gl))

	def test_sale_versus_cancellation_of_an_unrelated_return(self):
		for _ in range(3):
			self.race("y_return")

	def test_sale_versus_cancellation_of_an_unrelated_sale(self):
		for _ in range(2):
			self.race("y_sale")

	def test_sale_versus_cancellation_of_the_same_medicines_return(self):
		for _ in range(3):
			self.race("x_return")
