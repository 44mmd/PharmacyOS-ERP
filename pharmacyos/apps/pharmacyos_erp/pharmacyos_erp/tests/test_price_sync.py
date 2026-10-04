# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""N-05: the ERP price is authoritative — a change to Item Price alone reaches the Cloud.

Previously only an Item save queued a `catalog.changed` event, so a new price stayed on the website
until someone happened to edit the item. Now Item Price changes (insert, edit, delete, moving a price
to another item or list) and a change of the catalog's selling price list queue the event. Delivery
keeps the outbox guarantees: retried after an outage with the same id and bytes, newer states travel as
new events with a later `computed_at`, and stock is never touched by a price event.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, now_datetime

from pharmacyos_erp.tests.test_integration import ensure_api_branch, published_medicine
from pharmacyos_erp.tests.utils import make_batch, make_user, profile_roles, receive, set_settings

LIST = "Standard Selling"


class Delivery:
	def __init__(self, behaviour="ok"):
		self.behaviour, self.sent = behaviour, []

	def __call__(self, url, data, headers, timeout):
		self.sent.append({"id": headers["X-PharmacyOS-Event-Id"], "body": json.loads(data), "raw": data})
		if self.behaviour == "down":
			raise ConnectionError("cloud unreachable")

		class Ok:
			def raise_for_status(self):
				return None

		return Ok()


class TestItemPriceSync(IntegrationTestCase):
	def setUp(self):
		from frappe.utils.password import set_encrypted_password

		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "s" * 40, "outbound_secret")
		ensure_api_branch()
		self.item = published_medicine(f"PRICE-{frappe.generate_hash(length=6).upper()}").name
		self.batch = make_batch(self.item, f"PB-{self.item}", 300)
		receive(self.item, self.batch, 6)
		self.deliver()  # whatever creating the item queued has gone out

	def tearDown(self):
		set_settings(enable_outbound_events=0, outbound_endpoint=None)

	def deliver(self, behaviour="ok"):
		from pharmacyos_erp.integration import outbox

		sender = Delivery(behaviour)
		with patch("requests.post", side_effect=sender):
			for _ in range(20):  # the outbox sends 100 per run: drain whatever earlier tests left
				before = len(sender.sent)
				outbox.process_outbox()
				if len(sender.sent) - before < outbox.BATCH_SIZE:
					break
		return sender.sent

	def catalog(self, sent):
		return [
			s
			for s in sent
			if s["body"]["event"] == "catalog.changed" and s["body"]["data"]["item_code"] == self.item
		]

	def price_doc(self):
		return frappe.get_doc("Item Price", {"item_code": self.item, "price_list": LIST})

	def set_price(self, rate):
		doc = self.price_doc()
		doc.price_list_rate = rate
		doc.save()

	def age_events(self):
		frappe.db.sql(
			"update `tabPharmacyOS Sync Event` set modified=%s where reference_name=%s",
			(add_days(now_datetime(), -1), self.item),
		)

	def test_price_change_alone_reaches_the_cloud(self):
		item_modified = frappe.db.get_value("Item", self.item, "modified")
		self.set_price(1250)
		self.assertEqual(frappe.db.get_value("Item", self.item, "modified"), item_modified, "no Item save")
		events = self.catalog(self.deliver())
		self.assertEqual(len(events), 1)
		self.assertEqual(flt(events[0]["body"]["data"]["item"]["price"]), 1250)

	def test_new_and_deleted_prices_are_sent(self):
		self.price_doc().delete()
		gone = self.catalog(self.deliver())
		self.assertIsNone(gone[-1]["body"]["data"]["item"]["price"])  # the Cloud stops selling it
		frappe.get_doc(
			{"doctype": "Item Price", "item_code": self.item, "price_list": LIST, "price_list_rate": 990}
		).insert()
		back = self.deliver()
		self.assertEqual(flt(self.catalog(back)[-1]["body"]["data"]["item"]["price"]), 990)
		# with its current stock, so it is back on sale at once
		stock = [
			s
			for s in back
			if s["body"]["event"] == "availability.changed" and s["body"]["data"]["item_code"] == self.item
		]
		self.assertEqual(flt(stock[-1]["body"]["data"]["qty"]), 6)

	def test_buying_price_does_not_touch_the_website(self):
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": self.item,
				"price_list": "Standard Buying",
				"price_list_rate": 400,
			}
		).insert()
		self.assertFalse(self.catalog(self.deliver()))

	def test_retry_after_cloud_outage_resends_the_same_event(self):
		self.set_price(1300)
		down = self.catalog(self.deliver("down"))
		self.assertEqual(flt(down[0]["body"]["data"]["item"]["price"]), 1300)
		self.assertEqual(frappe.db.get_value("PharmacyOS Sync Event", down[0]["id"], "status"), "Failed")
		self.age_events()
		up = self.catalog(self.deliver())
		self.assertEqual([s["id"] for s in up], [down[0]["id"]])
		self.assertEqual(up[0]["raw"], down[0]["raw"], "a retry is byte-identical (duplicate-safe)")
		self.assertEqual(frappe.db.get_value("PharmacyOS Sync Event", down[0]["id"], "status"), "Sent")

	def test_newer_price_is_a_newer_event_and_older_never_wins(self):
		self.set_price(1100)
		first = self.catalog(self.deliver("down"))[0]  # the Cloud may have applied it; the response was lost
		self.set_price(1400)  # changed again before the retry
		self.age_events()
		sent = self.catalog(self.deliver())
		by_id = {s["id"]: s["body"] for s in sent}
		self.assertEqual(flt(by_id[first["id"]]["data"]["item"]["price"]), 1100)  # same id, same state
		newer = [b for i, b in by_id.items() if i != first["id"]]
		self.assertEqual(flt(newer[-1]["data"]["item"]["price"]), 1400)
		# the Cloud keeps the state with the latest computed_at, whatever order they arrive in
		self.assertGreater(newer[-1]["computed_at"], first["body"]["computed_at"])

	def test_price_change_never_changes_stock(self):
		from erpnext.stock.doctype.batch.batch import get_batch_qty

		before = flt(get_batch_qty(self.batch, "_Test Warehouse - _TC"))
		sle_count = frappe.db.count("Stock Ledger Entry", {"item_code": self.item})
		self.set_price(1700)
		sent = self.deliver()
		self.assertEqual(flt(get_batch_qty(self.batch, "_Test Warehouse - _TC")), before)
		self.assertEqual(frappe.db.count("Stock Ledger Entry", {"item_code": self.item}), sle_count)
		self.assertNotIn("qty", self.catalog(sent)[-1]["body"]["data"]["item"])

	def test_switching_the_selling_price_list_resends_published_items(self):
		settings = frappe.get_doc("Selling Settings")
		original = settings.selling_price_list
		other = (
			"_Test Price List" if frappe.db.exists("Price List", "_Test Price List") else "Standard Buying"
		)
		try:
			settings.selling_price_list = other
			settings.save()
			self.assertTrue(
				frappe.db.exists(
					"PharmacyOS Sync Event", {"dedupe_key": f"catalog:{self.item}", "status": "Pending"}
				)
			)
		finally:
			settings.reload()
			settings.selling_price_list = original
			settings.save()

	def test_catalog_pull_feed_includes_a_price_only_change(self):
		from pharmacyos_erp.api.v1 import catalog

		api_user = make_user("price-sync-api@example.test", profile_roles("PharmacyOS Integration"))
		old = add_days(now_datetime(), -30)
		frappe.db.set_value("Item", self.item, "modified", old, update_modified=False)
		# an old price row: saved and valid since a month ago (a price becoming valid also moves the feed)
		frappe.db.set_value(
			"Item Price", self.price_doc().name, {"modified": old, "valid_from": old.date()}, update_modified=False
		)
		since = add_days(now_datetime(), -1)
		frappe.set_user(api_user)
		try:
			before = [
				i["item_code"] for i in catalog.get_catalog(modified_since=str(since), limit=500)["items"]
			]
		finally:
			frappe.set_user("Administrator")
		self.assertNotIn(self.item, before)
		self.set_price(1999)
		frappe.set_user(api_user)
		try:
			after = {
				i["item_code"]: i for i in catalog.get_catalog(modified_since=str(since), limit=500)["items"]
			}
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(flt(after[self.item]["price"]), 1999)


class TestPublishingSendsStock(IntegrationTestCase):
	"""Stock received before an item is published must reach the website when it is published."""

	def setUp(self):
		from frappe.utils.password import set_encrypted_password

		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "s" * 40, "outbound_secret")
		ensure_api_branch()

	def tearDown(self):
		set_settings(enable_outbound_events=0, outbound_endpoint=None)

	def test_publishing_an_item_with_stock_sends_its_stock(self):
		from pharmacyos_erp.integration import outbox
		from pharmacyos_erp.tests.utils import make_medicine

		code = make_medicine(f"PUBLISH-{frappe.generate_hash(length=6).upper()}").name
		receive(code, make_batch(code, f"PUB-{code}", 300), 4)  # not published yet: no event
		item = frappe.get_doc("Item", code)
		item.pharmacyos_publish = 1
		item.save()
		sender = Delivery()
		with patch("requests.post", side_effect=sender):
			outbox.process_outbox()
		stock = [
			s["body"]["data"]
			for s in sender.sent
			if s["body"]["event"] == "availability.changed" and s["body"]["data"]["item_code"] == code
		]
		self.assertTrue(stock, "publishing must send the item's stock")
		self.assertEqual(flt(stock[-1]["qty"]), 4)
