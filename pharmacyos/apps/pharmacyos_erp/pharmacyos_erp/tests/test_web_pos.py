# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""The shared POS screen (/pos, web browser and Windows desktop): its server API is orchestration only.

Proves, with a real cashier (Cashier role profile) on the ERPNext test company:
* the sale is the same POS Sales Invoice the ERPNext POS creates, submitted as the cashier, with
  ERPNext's prices, totals, change and FEFO batch; expired batches are never sold;
* checkout is idempotent by request ID (a retry returns the first invoice, never a second sale);
* the counter's rate/discount switches and user list are enforced on the server;
* returns go through ERPNext's return mapper and the validated PharmacyOS return rules;
* guests get nothing; quotes save nothing.
HTTP-level checks (CSRF, cookies, concurrency, Cloud) are in `dev/web_pos_check.py`.
"""

import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, now_datetime, nowdate

from pharmacyos_erp.pos import api
from pharmacyos_erp.tests.test_pos_shift import ensure_pos_profile
from pharmacyos_erp.tests.utils import COMPANY, WAREHOUSE, make_batch, make_medicine, make_user, profile_roles, receive

CASHIER_ROLES = profile_roles("Cashier")
CASHIER = "web-pos-cashier@example.com"
OTHER = "web-pos-other@example.com"
COUNTER = "PharmacyOS Web POS Counter"
DISCOUNT_COUNTER = "PharmacyOS Web POS Discount Counter"


def rid() -> str:
	return str(uuid.uuid4())


def bin_qty(item, warehouse=WAREHOUSE) -> float:
	return flt(frappe.db.get_value("Bin", {"item_code": item, "warehouse": warehouse}, "actual_qty"))


def bundle_batches(invoice_name) -> dict:
	inv = frappe.get_doc("Sales Invoice", invoice_name)
	out = {}
	for row in inv.items:
		for e in frappe.get_all("Serial and Batch Entry", filters={"parent": row.serial_and_batch_bundle}, fields=["batch_no", "qty"]):
			out[e.batch_no] = out.get(e.batch_no, 0) + abs(flt(e.qty))
		if row.batch_no and not row.serial_and_batch_bundle:
			out[row.batch_no] = out.get(row.batch_no, 0) + abs(flt(row.stock_qty))
	return out


class TestWebPOS(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(CASHIER, CASHIER_ROLES)
		make_user(OTHER, CASHIER_ROLES)
		ensure_pos_profile(COUNTER, [CASHIER])
		ensure_pos_profile(DISCOUNT_COUNTER, [OTHER])
		frappe.db.set_value("POS Profile", DISCOUNT_COUNTER, "allow_discount_change", 1)
		frappe.clear_document_cache("POS Profile", DISCOUNT_COUNTER)
		cls.item = make_medicine("WEB-POS-FEFO", item_name="Web POS FEFO Medicine")
		frappe.db.set_value("Item", cls.item.name, "standard_rate", 1000)
		ensure_price(cls.item.name, 1000)
		# the later batch is received first: FEFO and FIFO disagree
		cls.late = make_batch(cls.item.name, "WP-LATE", 400)
		cls.early = make_batch(cls.item.name, "WP-EARLY", 40)
		receive(cls.item.name, cls.late, 20)
		receive(cls.item.name, cls.early, 20)
		for user, counter in ((CASHIER, COUNTER), (OTHER, DISCOUNT_COUNTER)):
			open_shift(user, counter)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")

	def sell(self, qty=1, user=CASHIER, counter=COUNTER, request_id=None, paid=None, **kw):
		frappe.set_user(user)
		return api.checkout(
			pos_profile=counter,
			items=[{"item_code": self.item.name, "qty": qty, **kw}],
			payments=[{"mode_of_payment": "Cash", "amount": paid if paid is not None else qty * 1000}],
			request_id=request_id or rid(),
		)

	def test_quote_is_erpnext_and_saves_nothing(self):
		frappe.set_user(CASHIER)
		before = frappe.db.count("Sales Invoice")
		quote = api.quote(pos_profile=COUNTER, items=[{"item_code": self.item.name, "qty": 3}])
		self.assertEqual((quote["grand_total"], quote["items"][0]["rate"]), (3000, 1000))
		self.assertEqual(frappe.db.count("Sales Invoice"), before)

	def test_cash_sale_is_the_pos_invoice_with_fefo_and_change(self):
		before = bin_qty(self.item.name)
		sale = self.sell(qty=2, paid=5000)
		inv = frappe.get_doc("Sales Invoice", sale["name"])
		self.assertEqual((inv.docstatus, inv.is_pos, inv.is_created_using_pos, inv.update_stock), (1, 1, 1, 1))
		self.assertEqual((inv.owner, inv.pos_profile, inv.status), (CASHIER, COUNTER, "Paid"))
		self.assertEqual((flt(inv.grand_total), flt(inv.change_amount)), (2000, 3000))
		self.assertEqual(bin_qty(self.item.name), before - 2)
		self.assertEqual(bundle_batches(inv.name), {self.early: 2})  # earliest expiry, not first received

	def test_checkout_is_idempotent_by_request_id(self):
		request_id = rid()
		before = bin_qty(self.item.name)
		first = self.sell(request_id=request_id)
		again = self.sell(request_id=request_id)
		self.assertEqual((again["name"], again["replayed"], first["replayed"]), (first["name"], True, False))
		self.assertEqual(bin_qty(self.item.name), before - 1)
		self.assertEqual(frappe.db.count("Sales Invoice", {api.REQUEST_FIELD: request_id}), 1)
		with self.assertRaises(frappe.PermissionError):
			self.sell(user=OTHER, counter=DISCOUNT_COUNTER, request_id=request_id)
		with self.assertRaises(api.POSRequestError):
			self.sell(request_id="short")

	def test_request_id_field_is_unique_and_never_copied(self):
		meta = frappe.get_meta("Sales Invoice").get_field(api.REQUEST_FIELD)
		self.assertTrue(meta.unique and meta.no_copy and meta.hidden)

	def test_expired_batch_is_never_sold(self):
		item = make_medicine("WEB-POS-EXPIRED", item_name="Web POS Expired Medicine")
		ensure_price(item.name, 1000)
		expired = make_batch(item.name, "WP-EXPIRED", -3)
		receive(item.name, expired, 5, posting_date=add_days(nowdate(), -20))
		frappe.set_user(CASHIER)
		found = api.search_items(pos_profile=COUNTER, search_term=item.name)["items"][0]
		self.assertEqual((found["sellable_qty"], found["actual_qty"]), (0, 5))
		for line in ({"item_code": item.name, "qty": 1}, {"item_code": item.name, "qty": 1, "batch_no": expired}):
			with self.assertRaises(frappe.ValidationError):
				api.checkout(pos_profile=COUNTER, items=[line], payments=[{"mode_of_payment": "Cash", "amount": 1000}], request_id=rid())
		frappe.set_user("Administrator")
		self.assertEqual(bin_qty(item.name), 5)

	def test_counter_switches_and_users_are_enforced(self):
		frappe.set_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			api.quote(pos_profile=COUNTER, items=[{"item_code": self.item.name, "qty": 1, "discount_percentage": 10}])
		with self.assertRaises(frappe.PermissionError):
			api.quote(pos_profile=COUNTER, items=[{"item_code": self.item.name, "qty": 1}], additional_discount_percentage=5)
		with self.assertRaises(frappe.PermissionError):
			api.quote(pos_profile=COUNTER, items=[{"item_code": self.item.name, "qty": 1, "rate": 1}])
		with self.assertRaises(frappe.PermissionError):
			api.quote(pos_profile=DISCOUNT_COUNTER, items=[{"item_code": self.item.name, "qty": 1}])
		frappe.set_user(OTHER)
		quote = api.quote(pos_profile=DISCOUNT_COUNTER, items=[{"item_code": self.item.name, "qty": 1, "discount_percentage": 10}])
		self.assertEqual(quote["grand_total"], 900)  # ERPNext applies the discount

	def test_counter_return_uses_the_validated_return_rules(self):
		sale = self.sell(qty=3)
		frappe.set_user(CASHIER)
		line = api.get_return_candidate(sale["name"])["lines"][0]
		self.assertEqual(line["returnable_qty"], 3)
		preview = api.preview_return(sale["name"], [{"row": line["row"], "qty": 1}])
		self.assertEqual(preview["grand_total"], -1000)
		before = bin_qty(self.item.name)
		ret = api.submit_return(sale["name"], [{"row": line["row"], "qty": 1}], request_id=rid())
		doc = frappe.get_doc("Sales Invoice", ret["name"])
		self.assertEqual((doc.is_return, doc.return_against, flt(doc.grand_total), doc.owner), (1, sale["name"], -1000, CASHIER))
		self.assertEqual(sum(flt(p.amount) for p in doc.payments), -1000)
		self.assertEqual(bin_qty(self.item.name), before + 1)
		self.assertEqual(bundle_batches(doc.name), {self.early: 1})  # back into the batch it was sold from
		with self.assertRaises(frappe.ValidationError):
			api.submit_return(sale["name"], [{"row": line["row"], "qty": 3}], request_id=rid())
		with self.assertRaises(api.POSRequestError):
			api.get_return_candidate(ret["name"])  # never a return against a return
		self.assertEqual(api.get_return_candidate(sale["name"])["lines"][0]["returnable_qty"], 2)

	def test_guest_gets_nothing(self):
		frappe.set_user("Guest")
		for call in (
			lambda: api.get_context(),
			lambda: api.quote(pos_profile=COUNTER, items=[{"item_code": self.item.name, "qty": 1}]),
			lambda: api.checkout(pos_profile=COUNTER, items=[], payments=[], request_id=rid()),
			lambda: api.recent_sales(),
		):
			with self.assertRaises((frappe.AuthenticationError, frappe.PermissionError)):
				call()

	def test_pos_page_requires_sign_in_and_staff(self):
		from pharmacyos_erp.www import pos

		frappe.set_user("Guest")
		with self.assertRaises(frappe.Redirect):
			pos.get_context(frappe._dict())
		self.assertEqual(frappe.local.flags.redirect_location, "/login?redirect-to=/pos")
		frappe.set_user(CASHIER)
		frappe.local.session.data.user_type = "System User"
		context = pos.get_context(frappe._dict())
		self.assertTrue(context.messages["complete_sale"])

	def test_context_lists_own_counter_and_shift(self):
		frappe.set_user(CASHIER)
		ctx = api.get_context()
		self.assertEqual(ctx["profile"]["name"], COUNTER)
		self.assertIn(COUNTER, [p["name"] for p in ctx["profiles"]])
		self.assertNotIn(DISCOUNT_COUNTER, [p["name"] for p in ctx["profiles"]])
		self.assertNotIn("valuation_rate", frappe.as_json(api.search_items(pos_profile=COUNTER, search_term=self.item.name)))


def ensure_price(item_code, rate):
	if not frappe.db.exists("Item Price", {"item_code": item_code, "price_list": "Standard Selling", "customer": ["is", "not set"]}):
		frappe.get_doc({"doctype": "Item Price", "item_code": item_code, "price_list": "Standard Selling", "price_list_rate": rate}).insert()
	else:
		frappe.db.set_value("Item Price", {"item_code": item_code, "price_list": "Standard Selling"}, "price_list_rate", rate)


def open_shift(user, counter):
	"""An open shift on `counter` today. A shift an earlier day's run left open is closed first, as the pharmacy
	would close it: ERPNext refuses every sale on a POS Opening Entry from another day ("outdated")."""
	for name, start in frappe.get_all(
		"POS Opening Entry",
		filters={"pos_profile": counter, "status": "Open", "docstatus": 1},
		fields=["name", "period_start_date"],
		as_list=True,
	):
		if str(start)[:10] == nowdate():
			return
		close_stale_shift(name)
	frappe.set_user(user)
	opening = frappe.get_doc(
		{
			"doctype": "POS Opening Entry",
			"pos_profile": counter,
			"company": COMPANY,
			"user": user,
			"period_start_date": now_datetime(),
			"posting_date": nowdate(),
			"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 0}],
		}
	)
	opening.insert()
	opening.submit()
	frappe.set_user("Administrator")


def close_stale_shift(name):
	"""ERPNext's own closing of a shift (POS Closing Entry, counted = expected), as the pharmacy closes a shift
	that was left open overnight."""
	from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import make_closing_entry_from_opening

	closing = make_closing_entry_from_opening(frappe.get_doc("POS Opening Entry", name))
	for row in closing.payment_reconciliation:
		row.closing_amount = row.expected_amount
	closing.insert()
	closing.submit()
