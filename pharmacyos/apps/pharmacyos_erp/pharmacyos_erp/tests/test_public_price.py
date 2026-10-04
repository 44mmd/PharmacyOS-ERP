# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""N-05 (round 3): the website shows the public selling price valid today — nothing else.

Two defects of the catalog price, both reproduced before the fix:

* any Item Price row of the selling list could become the storefront price — a customer-specific
  price of 400 was published instead of the public 1000;
* the validity dates were ignored and nothing happened at midnight: a price that expired yesterday
  (ERPNext: no price) was still published, so the website kept selling at it.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, getdate, now_datetime, nowdate

from pharmacyos_erp.api.v1.catalog import catalog_item, public_prices, selling_price_list
from pharmacyos_erp.tests.test_integration import CODE, ensure_api_branch, published_medicine
from pharmacyos_erp.tests.test_price_sync import Delivery
from pharmacyos_erp.tests.utils import make_batch, make_user, profile_roles, receive, set_settings


def erpnext_rate(item_code, on_date=None, customer=None):
	"""What ERPNext itself charges (the reference the public price must agree with)."""
	from erpnext.stock.get_item_details import get_price_list_rate_for

	ctx = frappe._dict(
		price_list=selling_price_list(),
		uom=frappe.db.get_value("Item", item_code, "stock_uom"),
		transaction_date=on_date or nowdate(),
		customer=customer,
		conversion_factor=1,
		qty=1,
	)
	return get_price_list_rate_for(ctx, item_code)


class PriceCase(IntegrationTestCase):
	def setUp(self):
		from frappe.utils.password import set_encrypted_password

		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "s" * 40, "outbound_secret")
		ensure_api_branch()
		self.list = selling_price_list()
		self.item = published_medicine(f"PUB-PRICE-{frappe.generate_hash(length=6).upper()}").name
		self.public = frappe.get_doc("Item Price", {"item_code": self.item, "price_list": self.list})
		self.assertEqual(flt(self.public.price_list_rate), 1000)
		self.customers = frappe.get_all("Customer", pluck="name", limit=2)
		self.deliver()

	def tearDown(self):
		# the outbox commits each delivery: leave nothing behind for later tests (the midnight job
		# looks at every published item)
		frappe.db.rollback()
		frappe.db.set_value("Item", self.item, "pharmacyos_publish", 0, update_modified=False)
		frappe.db.delete("PharmacyOS Sync Event", {"reference_name": self.item})
		frappe.db.commit()
		set_settings(enable_outbound_events=0, outbound_endpoint=None)

	def deliver(self, behaviour="ok"):
		from pharmacyos_erp.integration import outbox

		sender = Delivery(behaviour)
		with patch("requests.post", side_effect=sender):
			for _ in range(20):  # the outbox sends 100 per run; the date job can queue more than that
				before = len(sender.sent)
				outbox.process_outbox()
				if len(sender.sent) - before < outbox.BATCH_SIZE:
					break
		return [
			s
			for s in sender.sent
			if s["body"]["event"] == "catalog.changed" and s["body"]["data"]["item_code"] == self.item
		]

	def price(self, on_date=None):
		found = public_prices([self.item], on_date)
		return flt(found[self.item].rate) if self.item in found else None

	def add_price(self, rate, **fields):
		return frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": self.item,
				"price_list": fields.pop("price_list", self.list),
				"price_list_rate": rate,
				**fields,
			}
		).insert()


class TestCustomerPricesNeverPublic(PriceCase):
	def test_customer_price_cheaper_newer_and_several(self):
		for i, customer in enumerate(self.customers):
			self.add_price(400 - i * 50, customer=customer, valid_from=nowdate())  # cheaper and newer
		self.assertEqual(self.price(), 1000)
		self.assertEqual(flt(catalog_item(self.item)["price"]), 1000)
		self.assertEqual(flt(erpnext_rate(self.item)), 1000)  # ERPNext for an anonymous buyer agrees
		# (the customer still gets their own price at the counter)
		self.assertEqual(flt(erpnext_rate(self.item, customer=self.customers[0])), 400)

	def test_customer_price_insert_edit_delete_events_carry_the_public_price(self):
		row = self.add_price(400, customer=self.customers[0])
		row.price_list_rate = 300
		row.save()
		row.delete()
		events = self.deliver()
		self.assertTrue(events)
		for event in events:
			self.assertEqual(flt(event["body"]["data"]["item"]["price"]), 1000)

	def test_only_a_customer_price_means_no_public_price(self):
		self.add_price(400, customer=self.customers[0])
		self.public.delete()
		self.assertIsNone(self.price())
		self.assertIsNone(self.deliver()[-1]["body"]["data"]["item"]["price"])  # off sale, never 400

	def test_supplier_batch_and_other_uom_prices_are_not_public(self):
		supplier = frappe.db.get_value("Supplier", {}, "name")
		if supplier:
			# ERPNext clears the supplier of a selling-list row on save; imported data can still carry one
			row = self.add_price(100, valid_from=add_days(nowdate(), -1))
			frappe.db.set_value("Item Price", row.name, "supplier", supplier, update_modified=False)
		batch = make_batch(self.item, f"PP-{self.item}", 300)
		self.add_price(200, batch_no=batch)
		if not frappe.db.exists("UOM Conversion Detail", {"parent": self.item, "uom": "Box"}):
			item = frappe.get_doc("Item", self.item)
			if frappe.db.exists("UOM", "Box"):
				item.append("uoms", {"uom": "Box", "conversion_factor": 10})
				item.save()
				self.add_price(9000, uom="Box")
		self.assertEqual(self.price(), 1000)

	def test_buying_and_non_selling_lists_are_never_public(self):
		self.add_price(300, price_list="Standard Buying")
		self.assertEqual(self.price(), 1000)
		settings = frappe.get_doc("Selling Settings")
		original = settings.selling_price_list
		try:
			settings.selling_price_list = "Standard Buying"  # misconfigured: a buying list
			settings.save()
			self.assertIsNone(self.price(), "a buying list is never the public price list")
		finally:
			settings.reload()
			settings.selling_price_list = original
			settings.save()

	def test_disabling_the_selling_list_takes_prices_off_and_is_sent(self):
		price_list = frappe.get_doc("Price List", self.list)
		price_list.enabled = 0
		price_list.save()
		try:
			self.assertIsNone(self.price())
			self.assertIsNone(self.deliver()[-1]["body"]["data"]["item"]["price"])
		finally:
			price_list.reload()
			price_list.enabled = 1
			price_list.save()
		self.assertEqual(flt(self.deliver()[-1]["body"]["data"]["item"]["price"]), 1000)

	def test_out_of_order_events_keep_their_own_state(self):
		"""Each event carries the state of its first attempt and a later computed_at for later states."""
		row = self.add_price(400, customer=self.customers[0])
		first = self.deliver("down")[-1]
		row.delete()
		self.public.price_list_rate = 1100
		self.public.save()
		frappe.db.sql(
			"update `tabPharmacyOS Sync Event` set modified=%s where reference_name=%s",
			(add_days(now_datetime(), -1), self.item),
		)
		sent = {s["id"]: s for s in self.deliver()}
		self.assertEqual(sent[first["id"]]["raw"], first["raw"], "a retry is byte-identical")
		self.assertEqual(flt(first["body"]["data"]["item"]["price"]), 1000)
		newest = max(sent.values(), key=lambda s: s["body"]["computed_at"])
		self.assertEqual(flt(newest["body"]["data"]["item"]["price"]), 1100)


class TestPriceExpiry(PriceCase):
	def expire_after_today(self):
		self.public.valid_from = add_days(nowdate(), -30)
		self.public.valid_upto = nowdate()
		self.public.save()
		self.deliver()

	def test_valid_until_today_then_expired_tomorrow(self):
		self.expire_after_today()
		self.assertEqual(self.price(), 1000)
		self.assertEqual(catalog_item(self.item)["price_valid_until"], str(getdate(nowdate())))
		tomorrow = add_days(nowdate(), 1)
		self.assertIsNone(self.price(tomorrow))
		self.assertIsNone(erpnext_rate(self.item, tomorrow))  # agrees with ERPNext

	def test_midnight_sends_the_expiry_once(self):
		from pharmacyos_erp.integration import outbox

		self.expire_after_today()
		frappe.db.set_default(outbox.PRICE_DATE_KEY, nowdate())
		self.assertEqual(outbox.queue_price_validity_changes(nowdate()), 0, "same date: nothing")
		tomorrow = add_days(nowdate(), 1)
		with patch("pharmacyos_erp.api.v1.catalog.nowdate", return_value=str(tomorrow)):
			self.assertGreaterEqual(outbox.queue_price_validity_changes(tomorrow), 1)
			self.assertEqual(
				outbox.queue_price_validity_changes(tomorrow), 0, "a duplicate run sends nothing"
			)
			sent = self.deliver()
		self.assertEqual(len(sent), 1)
		self.assertIsNone(sent[0]["body"]["data"]["item"]["price"])  # the website stops selling it

	def test_server_off_over_midnight_catches_up(self):
		from pharmacyos_erp.integration import outbox

		self.expire_after_today()
		frappe.db.set_default(outbox.PRICE_DATE_KEY, str(add_days(nowdate(), -2)))  # off for days
		self.public.valid_upto = add_days(nowdate(), -1)  # expired yesterday, while the server was off
		self.public.save()
		self.deliver()
		frappe.db.set_default(outbox.PRICE_DATE_KEY, str(add_days(nowdate(), -2)))
		self.assertGreaterEqual(outbox.queue_price_validity_changes(nowdate()), 1)
		self.assertIsNone(self.deliver()[-1]["body"]["data"]["item"]["price"])

	def test_cloud_outage_at_expiry_then_recovery(self):
		from pharmacyos_erp.integration import outbox

		self.expire_after_today()
		frappe.db.set_default(outbox.PRICE_DATE_KEY, nowdate())
		tomorrow = add_days(nowdate(), 1)
		with patch("pharmacyos_erp.api.v1.catalog.nowdate", return_value=str(tomorrow)):
			outbox.queue_price_validity_changes(tomorrow)
			down = self.deliver("down")
			self.assertIsNone(down[-1]["body"]["data"]["item"]["price"])
			frappe.db.sql(
				"update `tabPharmacyOS Sync Event` set modified=%s where reference_name=%s",
				(add_days(now_datetime(), -1), self.item),
			)
			up = self.deliver()
		self.assertEqual([s["id"] for s in up], [down[-1]["id"]])
		self.assertEqual(up[-1]["raw"], down[-1]["raw"])

	def test_replacement_price_after_expiry(self):
		from pharmacyos_erp.integration import outbox

		self.expire_after_today()
		tomorrow, later = add_days(nowdate(), 1), add_days(nowdate(), 3)
		self.add_price(1200, valid_from=later)  # future price: not valid yet
		self.assertEqual(self.price(), 1000)
		self.assertIsNone(self.price(tomorrow), "the gap between the two prices: off sale")
		self.assertEqual(self.price(later), 1200)
		frappe.db.set_default(outbox.PRICE_DATE_KEY, str(tomorrow))
		self.deliver()
		with patch("pharmacyos_erp.api.v1.catalog.nowdate", return_value=str(later)):
			self.assertGreaterEqual(outbox.queue_price_validity_changes(later), 1)
			sent = self.deliver()
		self.assertEqual(flt(sent[-1]["body"]["data"]["item"]["price"]), 1200)
		self.assertIsNone(sent[-1]["body"]["data"]["item"]["price_valid_until"])

	def test_baghdad_date_decides_not_utc(self):
		"""22:30 UTC on the 4th is 01:30 on the 5th in Baghdad: a price valid until the 4th has expired."""
		from frappe.tests.classes.context_managers import freeze_time

		previous = frappe.db.get_single_value("System Settings", "time_zone")
		frappe.db.set_single_value("System Settings", "time_zone", "Asia/Baghdad")
		frappe.cache.delete_value("time_zone")
		try:
			self.public.valid_from = "2026-09-01"
			self.public.valid_upto = "2026-10-04"
			self.public.save()
			with freeze_time("2026-10-04 22:30:00", is_utc=True):
				self.assertEqual(nowdate(), "2026-10-05")
				self.assertIsNone(self.price())
				self.assertIsNone(catalog_item(self.item)["price"])
			with freeze_time("2026-10-04 20:30:00", is_utc=True):  # 23:30 in Baghdad: still the 4th
				self.assertEqual(self.price(), 1000)
		finally:
			frappe.db.set_single_value("System Settings", "time_zone", previous)
			frappe.cache.delete_value("time_zone")

	def test_pull_feed_after_expiry_lists_the_item_without_a_price(self):
		from pharmacyos_erp.api.v1 import catalog

		old = add_days(now_datetime(), -30)
		self.public.valid_from = add_days(nowdate(), -30)
		self.public.valid_upto = add_days(nowdate(), -1)  # expired at midnight; nothing saved since
		self.public.save()
		frappe.db.set_value("Item", self.item, "modified", old, update_modified=False)
		frappe.db.set_value("Item Price", self.public.name, "modified", old, update_modified=False)
		api_user = make_user("public-price-api@example.test", profile_roles("PharmacyOS Integration"))
		frappe.set_user(api_user)
		try:
			feed = catalog.get_catalog(modified_since=str(add_days(now_datetime(), -2)), limit=500)
		finally:
			frappe.set_user("Administrator")
		found = {i["item_code"]: i for i in feed["items"]}
		self.assertIn(self.item, found, "the expiry moves the item forward in the feed")
		self.assertIsNone(found[self.item]["price"])

	def test_website_order_for_an_expired_price_is_refused(self):
		from pharmacyos_erp.api.v1 import orders

		receive(self.item, make_batch(self.item, f"PX-{self.item}", 300), 3)
		self.public.valid_from = add_days(nowdate(), -30)
		self.public.valid_upto = add_days(nowdate(), -1)
		self.public.save()
		with self.assertRaises(frappe.ValidationError) as caught:
			orders.create_order(
				f"PX-{frappe.generate_hash(length=6)}",
				CODE,
				[{"item_code": self.item, "qty": 1}],
				{"id": "px", "name": "Expired"},
			)
		self.assertIn("No valid selling price", str(caught.exception))
		self.assertFalse(
			frappe.db.exists("Sales Order Item", {"item_code": self.item, "docstatus": 1}),
			"never ordered at a stale or zero price",
		)


class TestOutboxNeverStarves(PriceCase):
	def test_failed_events_in_back_off_do_not_hold_back_a_new_price(self):
		"""More than a batch of failed deliveries waiting to retry must not delay a price expiry."""
		from pharmacyos_erp.integration import outbox

		backlog = []
		for _ in range(outbox.BATCH_SIZE + 20):
			event = frappe.get_doc(
				{
					"doctype": "PharmacyOS Sync Event",
					"event_type": "catalog.changed",
					"reference_doctype": "Item",
					"reference_name": self.item,
					"dedupe_key": f"backlog:{frappe.generate_hash(length=8)}",
					"status": "Failed",
					"attempts": 1,
					"payload": "{}",
				}
			).insert(ignore_permissions=True)
			backlog.append(event.name)
		frappe.db.sql(  # older than the new event, failed a moment ago: in back-off
			"update `tabPharmacyOS Sync Event` set creation=%s, modified=%s where name in %s",
			(add_days(now_datetime(), -1), now_datetime(), tuple(backlog)),
		)
		self.public.price_list_rate = 1300
		self.public.save()
		from pharmacyos_erp.tests.test_price_sync import Delivery

		sender = Delivery()
		with patch("requests.post", side_effect=sender):
			outbox.process_outbox()  # one run, as the scheduler does every minute
		sent = [
			s
			for s in sender.sent
			if s["id"] not in backlog
			and s["body"]["event"] == "catalog.changed"
			and s["body"]["data"]["item_code"] == self.item
		]
		self.assertTrue(sent, "the new price event waited behind the back-off backlog")
		self.assertEqual(flt(sent[-1]["body"]["data"]["item"]["price"]), 1300)
		self.assertFalse(
			set(s["id"] for s in sender.sent) & set(backlog), "events in back-off are not retried early"
		)


class TestPublicPriceRound4(PriceCase):
	"""Round 4 (D-7): the public price is the one ERPNext would charge for a single unit today."""

	def test_packing_unit_row_is_not_a_public_per_unit_price(self):
		self.public.db_set({"packing_unit": 12, "price_list_rate": 900})
		frappe.clear_cache()
		self.assertIsNone(erpnext_rate(self.item), "ERPNext prices a single unit at nothing")
		self.assertNotIn(self.item, public_prices([self.item]))
		self.assertIsNone(catalog_item(self.item)["price"])
		self.public.db_set({"packing_unit": 0, "price_list_rate": 1000})
		frappe.clear_cache()
		self.assertEqual(public_prices([self.item])[self.item].rate, 1000)
		self.assertEqual(erpnext_rate(self.item), 1000)

	def test_disabled_item_has_no_public_price(self):
		frappe.db.set_value("Item", self.item, "disabled", 1)
		self.assertNotIn(self.item, public_prices([self.item]))
		entry = catalog_item(self.item)
		self.assertEqual((entry["price"], entry["disabled"]), (None, 1))
		frappe.db.set_value("Item", self.item, "disabled", 0)
		self.assertEqual(catalog_item(self.item)["price"], 1000)
