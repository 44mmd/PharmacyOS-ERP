# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""LOCAL ERP v1 operations: voids at the counter, expired-stock disposal, the pharmacy report, supplier
returns — each with its integrity rule and the role that may (or may not) do it, enforced on the server."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from pharmacyos_erp.pharmacy import disposal, reports
from pharmacyos_erp.pos import api
from pharmacyos_erp.tests.test_operations import make_receipt
from pharmacyos_erp.tests.test_pos_shift import ensure_pos_profile
from pharmacyos_erp.tests.test_web_pos import CASHIER_ROLES, bin_qty, bundle_batches, ensure_price, open_shift, rid
from pharmacyos_erp.tests.utils import WAREHOUSE, make_batch, make_medicine, make_user, profile_roles, receive

CASHIER = "v1-cashier@example.com"
MANAGER = "v1-manager@example.com"
INVENTORY = "v1-inventory@example.com"
PHARMACIST = "v1-pharmacist@example.com"
OWNER = "v1-owner@example.com"
COUNTER = "PharmacyOS V1 Counter"
MANAGER_PASSWORD = "Manager-Approves-2026!"


def set_password(user, password):
	from frappe.utils.password import update_password

	update_password(user, password)


def batch_qty(item, batch, warehouse=WAREHOUSE):
	from pharmacyos_erp.pharmacy.expiry import get_batch_stock

	frappe.set_user("Administrator")
	return sum(flt(r["qty"]) for r in get_batch_stock([warehouse], item) if r["batch_no"] == batch)


class TestV1Operations(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(CASHIER, CASHIER_ROLES)
		make_user(MANAGER, profile_roles("Pharmacy Manager"))
		make_user(INVENTORY, profile_roles("Inventory Manager"))
		make_user(PHARMACIST, profile_roles("Pharmacist"))
		make_user(OWNER, profile_roles("Pharmacy Owner"))
		set_password(MANAGER, MANAGER_PASSWORD)
		ensure_pos_profile(COUNTER, [CASHIER, MANAGER])
		open_shift(CASHIER, COUNTER)
		cls.item = make_medicine("V1-MED", item_name="V1 Medicine")
		ensure_price(cls.item.name, 1000)
		cls.batch = make_batch(cls.item.name, "V1-GOOD", 400)
		receive(cls.item.name, cls.batch, 50)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.cache.delete_value(f"{api.APPROVAL_FAILURES_KEY}:{CASHIER}")
		frappe.cache.delete_value(f"{api.APPROVAL_FAILURES_KEY}:approver:{MANAGER}")

	def sell(self, qty=2, user=CASHIER):
		frappe.set_user(user)
		return api.checkout(
			pos_profile=COUNTER,
			items=[{"item_code": self.item.name, "qty": qty}],
			payments=[{"mode_of_payment": "Cash", "amount": qty * 1000}],
			request_id=rid(),
		)

	# ------------------------------------------------------------------ voids

	def test_cashier_void_needs_a_manager_and_is_audited(self):
		before = bin_qty(self.item.name)
		sale = self.sell(3)
		self.assertEqual(bin_qty(self.item.name), before - 3)
		frappe.set_user(CASHIER)
		self.assertFalse(api.get_context()["can_void"])
		with self.assertRaises(frappe.PermissionError):
			api.void_sale(sale["name"], reason="wrong item")  # no approval
		with self.assertRaises(frappe.PermissionError):
			api.void_sale(sale["name"], reason="wrong item", approver=MANAGER, approver_password="not-it")
		with self.assertRaises(frappe.PermissionError):
			api.void_sale(sale["name"], reason="wrong item", approver=CASHIER, approver_password="x")  # not oneself
		self.assertEqual(frappe.db.get_value("Sales Invoice", sale["name"], "docstatus"), 1)

		frappe.local.session.sid = "cashier-browser-session"
		frappe.local.session.data = frappe._dict(csrf_token="cashier-token")
		voided = api.void_sale(sale["name"], reason="wrong item", approver=MANAGER, approver_password=MANAGER_PASSWORD)
		# the cashier keeps their own session (sign-in and CSRF token) after the manager's approval
		self.assertEqual((frappe.session.user, frappe.local.session.sid, frappe.local.session.data.csrf_token), (CASHIER, "cashier-browser-session", "cashier-token"))
		frappe.set_user("Administrator")
		self.assertEqual((voided["requested_by"], voided["approved_by"]), (CASHIER, MANAGER))
		self.assertEqual(frappe.db.get_value("Sales Invoice", sale["name"], "docstatus"), 2, "kept, cancelled — never deleted")
		self.assertEqual(bin_qty(self.item.name), before, "stock back")
		log = frappe.get_doc("PharmacyOS Void Log", voided["name"])
		self.assertEqual((log.invoice, flt(log.amount), log.reason, log.sale_owner), (sale["name"], 3000, "wrong item", CASHIER))
		# a repeated click returns the same void, nothing more happens
		frappe.set_user(CASHIER)
		again = api.void_sale(sale["name"], reason="wrong item", approver=MANAGER, approver_password=MANAGER_PASSWORD)
		self.assertEqual(again["name"], voided["name"])
		self.assertEqual(frappe.db.count("PharmacyOS Void Log", {"invoice": sale["name"]}), 1)
		self.assertTrue(any(r["name"] == sale["name"] and r["voided"] for r in api.recent_sales(search=sale["name"])))

	def test_failed_approvals_are_limited(self):
		sale = self.sell(1)
		frappe.set_user(CASHIER)
		for _ in range(api.MAX_APPROVAL_FAILURES):
			with self.assertRaises(frappe.PermissionError):
				api.void_sale(sale["name"], reason="test", approver=MANAGER, approver_password="guess")
		with self.assertRaises(frappe.PermissionError):  # even the right password is refused now
			api.void_sale(sale["name"], reason="test", approver=MANAGER, approver_password=MANAGER_PASSWORD)
		self.assertEqual(frappe.db.get_value("Sales Invoice", sale["name"], "docstatus"), 1)

	def test_manager_voids_directly_and_returned_sales_cannot_be_voided(self):
		sale = self.sell(2)
		frappe.set_user(CASHIER)
		line = api.get_return_candidate(sale["name"])["lines"][0]
		api.submit_return(sale["name"], [{"row": line["row"], "qty": 1}], request_id=rid())
		frappe.set_user(MANAGER)
		self.assertTrue(api.get_context()["can_void"])
		with self.assertRaises(api.POSRequestError):
			api.void_sale(sale["name"], reason="customer changed mind")
		other = self.sell(1)
		frappe.set_user(MANAGER)
		done = api.void_sale(other["name"], reason="duplicate sale")
		self.assertEqual(done["approved_by"], MANAGER)

	def test_voided_sale_leaves_the_shift_totals(self):
		frappe.set_user(CASHIER)
		expected_before = next(p for p in api.shift_summary()["payments"] if p["mode_of_payment"] == "Cash")["expected_amount"]
		sale = self.sell(4)
		frappe.set_user(CASHIER)
		api.void_sale(sale["name"], reason="test", approver=MANAGER, approver_password=MANAGER_PASSWORD)
		frappe.set_user(CASHIER)
		expected_after = next(p for p in api.shift_summary()["payments"] if p["mode_of_payment"] == "Cash")["expected_amount"]
		self.assertEqual(flt(expected_after), flt(expected_before), "a voided sale brings no cash")

	# ------------------------------------------------------------------ expired stock

	def test_expired_stock_is_disposed_with_an_audit_trail(self):
		suffix = frappe.generate_hash(length=6).upper()  # committed stock from an earlier run never mixes in
		item = make_medicine(f"V1-EXPIRED-{suffix}", item_name="V1 Expired Medicine")
		expired = make_batch(item.name, f"V1-EXP-{suffix}", -10)
		receive(item.name, expired, 10, posting_date=add_days(nowdate(), -30))  # received while still valid
		frappe.db.commit()
		self.assertEqual(batch_qty(item.name, expired), 10)

		for user in (CASHIER, PHARMACIST):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				disposal.dispose_batch(item.name, expired, WAREHOUSE, 1, "Expired")

		frappe.set_user(INVENTORY)
		self.assertTrue(disposal.can_dispose())
		with self.assertRaises(frappe.ValidationError):
			disposal.dispose_batch(item.name, expired, WAREHOUSE, 11, "Expired")  # more than there is
		with self.assertRaises(frappe.ValidationError):
			disposal.dispose_batch(item.name, expired, WAREHOUSE, 1, "Other")  # Other needs a note
		done = disposal.dispose_batch(item.name, expired, WAREHOUSE, 4, "Expired", note="found at stock count")
		entry = frappe.get_doc("Stock Entry", done["name"])
		self.assertEqual((entry.docstatus, entry.stock_entry_type, entry.pharmacyos_disposal_reason, entry.owner), (1, disposal.DISPOSAL_TYPE, "Expired", INVENTORY))
		self.assertIn("found at stock count", entry.remarks)
		self.assertEqual(batch_qty(item.name, expired), 6)
		history = disposal.get_disposals()
		self.assertEqual(history[0]["name"], done["name"])
		self.assertEqual((history[0]["items"][0]["qty"], history[0]["reason"]), (4, "Expired"))

	def test_a_healthy_batch_is_not_disposed_as_expired(self):
		frappe.set_user(INVENTORY)
		with self.assertRaises(frappe.ValidationError):
			disposal.dispose_batch(self.item.name, self.batch, WAREHOUSE, 1, "Expired")
		done = disposal.dispose_batch(self.item.name, self.batch, WAREHOUSE, 1, "Damaged")
		self.assertEqual(frappe.db.get_value("Stock Entry", done["name"], "pharmacyos_disposal_reason"), "Damaged")

	def test_expired_batch_cannot_be_redated_into_sellable_stock(self):
		item = make_medicine("V1-REDATE", item_name="V1 Redate Medicine")
		batch = make_batch(item.name, "V1-RD-01", -3)
		frappe.set_user(INVENTORY)
		doc = frappe.get_doc("Batch", batch)
		doc.expiry_date = add_days(nowdate(), 300)
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		doc = frappe.get_doc("Batch", batch)
		doc.expiry_date = add_days(nowdate(), -30)  # earlier is always allowed
		doc.save()
		# an EXPIRED batch is never re-dated, not even by the owner: it is disposed of
		frappe.set_user(OWNER)
		doc = frappe.get_doc("Batch", batch)
		doc.expiry_date = add_days(nowdate(), 5)
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		# a batch that has not expired yet: only the owner may correct a mistyped date later, and the
		# correction is kept in the batch's history
		fresh = make_batch(item.name, f"V1-RD-{frappe.generate_hash(length=6)}", 20)
		frappe.set_user(INVENTORY)
		doc = frappe.get_doc("Batch", fresh)
		doc.expiry_date = add_days(nowdate(), 400)
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		frappe.set_user(OWNER)
		doc = frappe.get_doc("Batch", fresh)
		doc.expiry_date = add_days(nowdate(), 400)
		doc.save(ignore_version=False)  # (Frappe skips version history in tests unless asked)
		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("Version", {"ref_doctype": "Batch", "docname": fresh}), "expiry correction recorded")

	# ------------------------------------------------------------------ reporting

	def test_report_is_for_managers_and_counts_the_day(self):
		sale = self.sell(2)
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			reports.get_sales_report()
		frappe.set_user(PHARMACIST)
		with self.assertRaises(frappe.PermissionError):
			reports.get_sales_report()
		frappe.set_user(MANAGER)
		report = reports.get_sales_report(nowdate(), nowdate())
		self.assertGreaterEqual(report["sales"]["transactions"], 1)
		self.assertTrue(any(c["user"] == CASHIER for c in report["cashiers"]))
		cash = next(p for p in report["payments"] if p["mode_of_payment"] == "Cash")
		self.assertGreaterEqual(cash["amount"], 2000)
		self.assertTrue(report["costs_visible"])
		self.assertIsNotNone(report["stock_value"])
		self.assertTrue(any(s["user"] == CASHIER for s in report["shifts"]))
		self.assertIn(sale["name"], frappe.get_all("Sales Invoice", filters={"owner": CASHIER}, pluck="name"))

	# ------------------------------------------------------------------ purchasing

	def test_supplier_return_takes_stock_back_out_of_the_batch(self):
		from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_return

		item = make_medicine("V1-SUPPLIER", item_name="V1 Supplier Medicine")
		batch = make_batch(item.name, "V1-SR-01", 500)
		receipt = make_receipt(item.name, batch, qty=10, submit=True)
		self.assertEqual(batch_qty(item.name, batch), 10)
		ret = make_purchase_return(receipt.name)
		for row in ret.items:
			row.qty = -3
			row.received_qty = -3
		ret.insert()
		ret.submit()
		self.assertEqual((ret.is_return, ret.return_against), (1, receipt.name))
		self.assertEqual(batch_qty(item.name, batch), 7)
		self.assertEqual(frappe.db.get_value("Purchase Receipt", receipt.name, "docstatus"), 1, "the receipt stays in history")
