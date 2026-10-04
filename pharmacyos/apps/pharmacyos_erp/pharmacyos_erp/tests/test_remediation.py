# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Regression tests for the release-candidate remediation.

Every test here guards an invariant an independent validation found broken:

* F-01 fresh-install Branch dimension columns and the health check;
* F-02 / F-21 least privilege for Cashier, Pharmacist and the integration user — exercised over HTTP
  through the same routed endpoints the POS and the Cloud use;
* F-03 returns can never exceed what was sold, however the return is built;
* F-07 counter sales cannot take units reserved for website orders;
* F-08 outbox events are immutable per id and versioned;
* F-13 a concurrent duplicate order id resolves to the existing order;
* F-14 first-run setup is idempotent once initialised;
* F-19 integration addresses must use HTTPS;
* F-22 counter sessions expire after a shift of inactivity.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, get_test_client, now_datetime, nowdate
from frappe.utils.password import update_password

from pharmacyos_erp.tests.test_integration import CODE, ensure_api_branch, published_medicine
from pharmacyos_erp.tests.test_pos_shift import ensure_pos_profile
from pharmacyos_erp.tests.utils import (
	COMPANY,
	CUSTOMER,
	WAREHOUSE,
	make_batch,
	make_medicine,
	make_user,
	profile_roles,
	receive,
	set_settings,
)

PASSWORD = "Remediation-Pass-123"


def ensure_cash_mode():
	cash = frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Cash", "is_group": 0}, "name")
	mop = frappe.get_doc("Mode of Payment", "Cash")
	if not any(a.company == COMPANY for a in mop.accounts):
		mop.append("accounts", {"company": COMPANY, "default_account": cash})
		mop.save()


def sale_doc(item_code, qty, batch=None, rate=1000, is_return=False, return_against=None, pos_profile=None):
	row = {
		"item_code": item_code,
		"qty": -qty if is_return else qty,
		"rate": rate,
		"warehouse": WAREHOUSE,
		"income_account": "Sales - _TC",
		"expense_account": "Cost of Goods Sold - _TC",
		"cost_center": "_Test Cost Center - _TC",
	}
	if batch:
		row.update({"batch_no": batch, "use_serial_batch_fields": 1})
	doc = {
		"doctype": "Sales Invoice",
		"company": COMPANY,
		"customer": CUSTOMER,
		"currency": "INR",
		"debit_to": "Debtors - _TC",
		"is_pos": 1,
		"update_stock": 1,
		"is_return": 1 if is_return else 0,
		"return_against": return_against,
		"items": [row],
		"payments": [{"mode_of_payment": "Cash", "amount": (-1 if is_return else 1) * qty * rate}],
	}
	if pos_profile:
		doc.update({"pos_profile": pos_profile, "is_created_using_pos": 1})
	return doc


def submit_sale(*args, **kwargs):
	si = frappe.get_doc(sale_doc(*args, **kwargs))
	si.insert()
	si.submit()
	return si


# --------------------------------------------------------------------------- F-01


class TestFreshInstallDimension(IntegrationTestCase):
	def test_dimension_fields_are_created_without_a_worker(self):
		"""Outside tests ERPNext only queues field creation; PharmacyOS must not depend on that job."""
		from pharmacyos_erp.pharmacy import branches

		created = []
		with (
			patch.object(frappe, "in_test", False),
			patch("frappe.enqueue") as enqueue,
			patch(
				"erpnext.accounts.doctype.accounting_dimension.accounting_dimension.make_dimension_in_accounting_doctypes",
				side_effect=lambda doc, doclist=None: created.append(list(doclist or [])),
			),
			patch.object(branches, "missing_branch_fields", return_value=["Budget", "GL Entry"]),
		):
			branches.ensure_branch_dimension()
		self.assertEqual(created, [["Budget", "GL Entry"]])  # synchronous, in this call
		self.assertFalse(
			[c for c in enqueue.call_args_list if "make_dimension" in str(c)],
			"field creation must not be left to a background job",
		)

	def test_health_check_fails_when_a_branch_column_is_missing(self):
		from pharmacyos_erp.backup.service import health_check

		self.assertTrue(health_check()["critical_branch_fields"])
		real = frappe.db.has_column
		with patch.object(
			frappe.db, "has_column", side_effect=lambda dt, col: False if dt == "GL Entry" else real(dt, col)
		):
			result = health_check()
		self.assertFalse(result["ok"])
		self.assertIn("GL Entry", result["missing_branch_fields"])
		self.assertFalse(result["critical_branch_fields"])

	def test_migrate_repairs_missing_columns(self):
		from pharmacyos_erp.pharmacy import branches

		with (
			patch.object(branches, "missing_branch_fields", return_value=["Budget"]),
			patch(
				"erpnext.accounts.doctype.accounting_dimension.accounting_dimension.make_dimension_in_accounting_doctypes"
			) as make,
		):
			from pharmacyos_erp.setup.install import ensure_structure

			ensure_structure()
		self.assertEqual(make.call_args.kwargs["doclist"], ["Budget"])


# --------------------------------------------------------------------------- F-02 / F-21 over HTTP


class TestLeastPrivilegeOverHTTP(IntegrationTestCase):
	"""Real logins and real routed API calls, as the POS screen and the Cloud make them."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()
		ensure_api_branch()
		cls.tag = frappe.generate_hash(length=6).upper()
		cls.users = {}
		for label, profile in (
			("cashier", "Cashier"),
			("pharmacist", "Pharmacist"),
			("integration", "PharmacyOS Integration"),
		):
			# fresh accounts per run: a shift left open by an interrupted run must not block this one
			email = make_user(f"remed-{label}-{cls.tag.lower()}@example.com", profile_roles(profile))
			update_password(email, PASSWORD)
			cls.users[label] = email
		cls.item = published_medicine(f"REMED-HTTP-{cls.tag}")
		cls.batch = make_batch(cls.item.name, f"RH-{cls.tag}", 300)
		receive(cls.item.name, cls.batch, 20)
		cls.profile = ensure_pos_profile(f"Remediation Counter {cls.tag}", [cls.users["cashier"]])
		frappe.db.commit()  # requests run on their own database connection

	def client(self, label):
		from frappe.tests.test_api import make_request

		client = get_test_client()
		response = make_request(
			client.post, ("/api/method/login",), {"json": {"usr": self.users[label], "pwd": PASSWORD}}
		)
		self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
		return client

	def call(self, client, method, **data):
		from frappe.tests.test_api import make_request

		response = make_request(client.post, (f"/api/method/{method}",), {"json": data})
		return response.status_code, response.json

	def get(self, client, method, **params):
		from frappe.tests.test_api import make_request

		response = make_request(client.get, (f"/api/method/{method}",), {"query_string": params})
		return response.status_code, response.json

	def insert(self, client, doc):
		return self.call(client, "frappe.client.insert", doc=doc)

	def journal(self):
		return {
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": COMPANY,
			"posting_date": nowdate(),
			"accounts": [
				{"account": "Cash - _TC", "debit_in_account_currency": 777, "cost_center": "_Test Cost Center - _TC"},
				{"account": "Sales - _TC", "credit_in_account_currency": 777, "cost_center": "_Test Cost Center - _TC"},
			],
		}

	def payment(self):
		return {
			"doctype": "Payment Entry",
			"payment_type": "Receive",
			"company": COMPANY,
			"party_type": "Customer",
			"party": CUSTOMER,
			"paid_from": "Debtors - _TC",
			"paid_to": "Cash - _TC",
			"paid_amount": 500,
			"received_amount": 500,
			"reference_no": "remediation",
			"reference_date": nowdate(),
		}

	def stock_receipt(self):
		return {
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": COMPANY,
			"items": [
				{
					"item_code": self.item.name,
					"qty": 100,
					"t_warehouse": WAREHOUSE,
					"basic_rate": 1,
					"batch_no": self.batch,
					"use_serial_batch_fields": 1,
				}
			],
		}

	def assert_denied(self, result, what):
		status, body = result
		self.assertEqual(status, 403, f"{what} must be denied, got {status}: {json.dumps(body)[:300]}")

	def test_cashier_runs_a_shift_and_sells_but_cannot_post_accounting_or_stock(self):
		cashier = self.client("cashier")
		# the POS screen's own server calls
		status, profile = self.call(
			cashier, "erpnext.selling.page.point_of_sale.point_of_sale.get_pos_profile_data", pos_profile=self.profile
		)
		self.assertEqual(status, 200, profile)
		status, items = self.call(
			cashier,
			"erpnext.selling.page.point_of_sale.point_of_sale.get_items",
			start=0,
			page_length=20,
			price_list="Standard Selling",
			item_group="All Item Groups",
			pos_profile=self.profile,
			search_term=self.item.name,
		)
		self.assertEqual(status, 200, items)
		status, opening = self.call(
			cashier,
			"erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher",
			pos_profile=self.profile,
			company=COMPANY,
			balance_details=[{"mode_of_payment": "Cash", "opening_amount": 10000}],
		)
		self.assertEqual(status, 200, opening)
		# a counter sale, saved then submitted as the POS screen does
		status, draft = self.insert(cashier, sale_doc(self.item.name, 2, self.batch, pos_profile=self.profile))
		self.assertEqual(status, 200, draft)
		status, submitted = self.call(cashier, "frappe.client.submit", doc=draft["message"])
		self.assertEqual(status, 200, submitted)
		self.assertEqual(submitted["message"]["docstatus"], 1)

		self.assert_denied(self.insert(cashier, self.journal()), "Journal Entry")
		self.assert_denied(self.insert(cashier, self.payment()), "Payment Entry")
		self.assert_denied(self.insert(cashier, self.stock_receipt()), "Stock Entry")
		self.assert_denied(self.call(cashier, "frappe.client.get_list", doctype="GL Entry"), "GL Entry read")
		self.assert_denied(
			self.call(cashier, "frappe.client.get_list", doctype="Payment Ledger Entry"), "ledger read"
		)
		self.assert_denied(
			# read-only for Sales User by ERPNext design (invoice forms need it); changing it is not allowed
			self.call(
				cashier,
				"frappe.client.set_value",
				doctype="Accounts Settings",
				name="Accounts Settings",
				fieldname="unlink_payment_on_cancellation_of_invoice",
				value=1,
			),
			"changing accounting settings",
		)
		self.assert_denied(
			self.call(
				cashier, "frappe.client.cancel", doctype="Sales Invoice", name=submitted["message"]["name"]
			),
			"invoice cancel",
		)

		# own shift closes with the cash taken during it (in-process: same server-side permissions)
		from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
			make_closing_entry_from_opening,
		)

		frappe.set_user(self.users["cashier"])
		try:
			closing = make_closing_entry_from_opening(frappe.get_doc("POS Opening Entry", opening["message"]["name"]))
			cash = next(p for p in closing.payment_reconciliation if p.mode_of_payment == "Cash")
			self.assertEqual(flt(cash.expected_amount), 2000)  # cash taken during this shift
			cash.closing_amount = 2000
			closing.insert()
			closing.submit()
		finally:
			frappe.set_user("Administrator")
		frappe.db.commit()

	def test_pharmacist_sells_and_reads_stock_but_cannot_create_stock_or_accounting(self):
		pharmacist = self.client("pharmacist")
		status, draft = self.insert(pharmacist, sale_doc(self.item.name, 1, self.batch))
		self.assertEqual(status, 200, draft)
		status, body = self.call(pharmacist, "frappe.client.submit", doc=draft["message"])
		self.assertEqual(status, 200, body)
		status, body = self.call(
			pharmacist,
			"frappe.client.get_list",
			doctype="Stock Ledger Entry",
			filters={"item_code": self.item.name},
			fields=["actual_qty"],
		)
		self.assertEqual(status, 200, body)
		status, body = self.call(pharmacist, "frappe.client.get_list", doctype="Batch", filters={"item": self.item.name})
		self.assertEqual(status, 200, body)
		self.assert_denied(self.insert(pharmacist, self.stock_receipt()), "Stock Entry")
		self.assert_denied(self.insert(pharmacist, self.journal()), "Journal Entry")
		self.assert_denied(self.insert(pharmacist, self.payment()), "Payment Entry")
		self.assert_denied(self.call(pharmacist, "frappe.client.get_list", doctype="GL Entry"), "GL Entry read")

	def test_integration_user_orders_but_has_no_stock_or_user_access(self):
		api = self.client("integration")
		status, body = self.call(
			api,
			"pharmacyos_erp.api.v1.orders.create_order",
			order_id=f"REMED-{self.tag}",
			branch_code=CODE,
			items=[{"item_code": self.item.name, "qty": 1}],
			customer={"id": f"remed-{self.tag}", "name": "Remediation"},
		)
		self.assertEqual(status, 200, body)
		status, body = self.get(
			api, "pharmacyos_erp.api.v1.availability.get_availability", branch_code=CODE, item_codes=self.item.name
		)
		self.assertEqual(status, 200, body)
		self.assert_denied(self.insert(api, self.stock_receipt()), "Stock Entry")
		self.assert_denied(self.insert(api, self.journal()), "Journal Entry")
		self.assert_denied(self.insert(api, sale_doc(self.item.name, 1, self.batch)), "Sales Invoice")
		# staff accounts (whose ids are their email addresses) are invisible to the Cloud's credentials
		status, body = self.call(api, "frappe.client.get_list", doctype="User", limit_page_length=0)
		self.assertEqual(status, 200, body)
		self.assertEqual([u["name"] for u in body["message"]], [self.users["integration"]])
		status, body = self.call(
			api, "frappe.desk.search.search_link", doctype="User", txt="remed", page_length=50
		)
		self.assertEqual(status, 200, body)
		self.assertLessEqual({r["value"] for r in body["message"]}, {self.users["integration"]})
		self.assert_denied(
			self.call(api, "frappe.client.get", doctype="User", name=self.users["cashier"]), "User read"
		)
		self.assert_denied(
			self.call(api, "frappe.client.get_value", doctype="PharmacyOS Settings", fieldname="cloud_base_url"),
			"settings read",
		)


	def test_simultaneous_identical_orders_create_one_and_all_get_it(self):
		"""Cloud retries racing each other: one Sales Order, and every request receives it (no 404/500)."""
		from frappe.tests.test_api import ThreadWithReturnValue

		clients = [self.client("integration") for _ in range(4)]
		body = {
			"order_id": f"REMED-PAR-{self.tag}",
			"branch_code": CODE,
			"items": [{"item_code": self.item.name, "qty": 1}],
			"customer": {"id": f"remed-par-{self.tag}", "name": "Parallel"},
		}
		threads = [
			ThreadWithReturnValue(
				target=client.post,
				args=("/api/method/pharmacyos_erp.api.v1.orders.create_order",),
				kwargs={"json": body},
			)
			for client in clients
		]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join()
		responses = [thread._return for thread in threads]
		self.assertEqual(
			[r.status_code for r in responses], [200] * 4, [r.get_data(as_text=True)[:200] for r in responses]
		)
		self.assertEqual(len({r.json["message"]["sales_order"] for r in responses}), 1)
		self.assertEqual(sum(1 for r in responses if r.json["message"]["created"]), 1)
		frappe.db.rollback()  # fresh snapshot to count what the requests committed
		self.assertEqual(frappe.db.count("Sales Order", {"pharmacyos_order_id": body["order_id"]}), 1)


class TestRoleProfiles(IntegrationTestCase):
	def test_counter_profiles_hold_no_accounting_or_stock_roles(self):
		from pharmacyos_erp.setup.install import ensure_role_profiles

		ensure_role_profiles()
		for profile in ("Cashier", "Pharmacist", "PharmacyOS Integration"):
			roles = {r.role for r in frappe.get_doc("Role Profile", profile).roles}
			self.assertFalse(roles & {"Accounts User", "Accounts Manager", "Stock User", "Stock Manager"}, profile)

	def test_upgrade_revokes_roles_from_existing_profile_users(self):
		from pharmacyos_erp.setup.install import ensure_role_profiles

		profile = frappe.get_doc("Role Profile", "Cashier")
		profile.append("roles", {"role": "Accounts User"})
		profile.flags.ignore_permissions = True
		profile.save()
		user = make_user("remed-profiled-cashier@example.com", [])
		doc = frappe.get_doc("User", user)
		doc.set("role_profiles", [{"role_profile": "Cashier"}])
		doc.save(ignore_permissions=True)
		self.assertIn("Accounts User", frappe.get_roles(user))
		with patch.object(frappe, "in_test", False):  # as in migrate: Frappe queues and locks the profile
			ensure_role_profiles()
		frappe.clear_cache(user=user)
		self.assertNotIn("Accounts User", frappe.get_roles(user))
		self.assertIn("Cashier", frappe.get_roles(user))
		# re-running (e.g. migrate twice, no worker) must not hit "Document Queued"
		frappe.get_doc("Role Profile", "Cashier").save(ignore_permissions=True)


# --------------------------------------------------------------------------- F-03


class TestReturnIntegrity(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()
		cls.item = make_medicine("REMED-RETURN-MED", item_name="Return Integrity Medicine")
		cls.batch = make_batch(cls.item.name, "REMED-RET-1", 300)
		cls.other_batch = make_batch(cls.item.name, "REMED-RET-2", 400)
		receive(cls.item.name, cls.batch, 50)
		receive(cls.item.name, cls.other_batch, 50)

	def batch_qty(self, batch):
		from erpnext.stock.doctype.batch.batch import get_batch_qty

		return flt(get_batch_qty(batch, WAREHOUSE))

	def test_partial_remaining_and_full_returns_then_nothing_more(self):
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		sale = submit_sale(self.item.name, 4, self.batch)
		after_sale = self.batch_qty(self.batch)
		submit_sale(self.item.name, 1, self.batch, is_return=True, return_against=sale.name)
		submit_sale(self.item.name, 3, self.batch, is_return=True, return_against=sale.name)
		self.assertEqual(self.batch_qty(self.batch), after_sale + 4)
		with self.assertRaises(StockOverReturnError):  # cumulative 5 > 4 sold
			submit_sale(self.item.name, 1, self.batch, is_return=True, return_against=sale.name)
		self.assertEqual(self.batch_qty(self.batch), after_sale + 4)  # no phantom stock

	def test_single_return_larger_than_the_sale_is_rejected(self):
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		sale = submit_sale(self.item.name, 2, self.batch)
		with self.assertRaises(StockOverReturnError):
			submit_sale(self.item.name, 3, self.batch, is_return=True, return_against=sale.name)

	def test_linked_return_from_the_return_button_still_works(self):
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

		sale = submit_sale(self.item.name, 2, self.batch)
		ret = make_sales_return(sale.name)
		ret.insert()
		ret.submit()
		gl = frappe.get_all("GL Entry", filters={"voucher_no": ret.name, "is_cancelled": 0}, fields=["debit", "credit"])
		self.assertAlmostEqual(sum(g.debit for g in gl), sum(g.credit for g in gl))
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		with self.assertRaises(StockOverReturnError):
			submit_sale(self.item.name, 1, self.batch, is_return=True, return_against=sale.name)

	def test_return_of_a_batch_that_was_not_sold_is_rejected(self):
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		sale = submit_sale(self.item.name, 2, self.batch)
		with self.assertRaises(StockOverReturnError):
			submit_sale(self.item.name, 1, self.other_batch, is_return=True, return_against=sale.name)

	def test_refund_rate_cannot_exceed_rate_charged(self):
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		sale = submit_sale(self.item.name, 2, self.batch, rate=1000)
		with self.assertRaises(StockOverReturnError):
			submit_sale(self.item.name, 1, self.batch, rate=9999, is_return=True, return_against=sale.name)

	def test_item_not_on_the_sale_cannot_be_returned(self):
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		other = make_medicine("REMED-RETURN-OTHER")
		other_batch = make_batch(other.name, "REMED-RET-OTHER", 300)
		receive(other.name, other_batch, 5)
		sale = submit_sale(self.item.name, 1, self.batch)
		with self.assertRaises((StockOverReturnError, frappe.ValidationError)):
			submit_sale(other.name, 1, other_batch, is_return=True, return_against=sale.name)

	def test_medicine_return_without_original_sale_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			submit_sale(self.item.name, 1, self.batch, is_return=True)

	def test_return_created_over_http_is_validated_too(self):
		"""The same rule applies to a return posted through the REST API."""
		from frappe.tests.test_api import make_request

		sale = submit_sale(self.item.name, 1, self.batch)
		frappe.db.commit()
		client = get_test_client()
		make_request(client.post, ("/api/method/login",), {"json": {"usr": "Administrator", "pwd": "admin"}})
		doc = {**sale_doc(self.item.name, 2, self.batch, is_return=True, return_against=sale.name), "docstatus": 1}
		response = make_request(client.post, ("/api/method/frappe.client.insert",), {"json": {"doc": doc}})
		self.assertNotEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
		self.assertIn("StockOverReturnError", response.get_data(as_text=True))


# --------------------------------------------------------------------------- F-07


class TestReservationProtection(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()
		ensure_api_branch()
		cls.api_user = make_user("remed-api-reserve@example.test", profile_roles("PharmacyOS Integration"))

	def setUp(self):
		self.tag = frappe.generate_hash(length=6).upper()
		self.item = published_medicine(f"REMED-RES-{self.tag}")
		self.good = make_batch(self.item.name, f"RR-{self.tag}", 300)
		self.expired = make_batch(self.item.name, f"RX-{self.tag}", -2)
		receive(self.item.name, self.good, 1)
		receive(self.item.name, self.expired, 3, posting_date=add_days(nowdate(), -20))

	def tearDown(self):
		set_settings(protect_online_reservations=1)
		frappe.set_user("Administrator")

	def reserve_last_unit(self):
		from pharmacyos_erp.api.v1 import orders

		frappe.set_user(self.api_user)
		try:
			return orders.create_order(
				f"REMED-RES-{self.tag}", CODE, [{"item_code": self.item.name, "qty": 1}], {"id": "remed-res"}
			)
		finally:
			frappe.set_user("Administrator")

	def test_counter_cannot_sell_the_unit_reserved_for_a_website_order(self):
		self.reserve_last_unit()
		with self.assertRaises(frappe.ValidationError) as ctx:
			submit_sale(self.item.name, 1)  # automatic batch pick
		self.assertIn("reserved for website orders", str(ctx.exception))
		with self.assertRaises(frappe.ValidationError):
			submit_sale(self.item.name, 1, self.good)

	def test_expired_units_do_not_count_as_free_stock(self):
		"""4 on hand, 3 of them expired, 1 reserved: nothing is free for the counter."""
		from pharmacyos_erp.pharmacy.stock_guard import current_free_qty, lock_bins

		self.reserve_last_unit()
		lock_bins([(self.item.name, WAREHOUSE)])
		free, reserved = current_free_qty(self.item.name, [WAREHOUSE], True)
		self.assertEqual((free, reserved), (0.0, 1.0))

	def test_second_website_order_for_the_last_unit_is_refused(self):
		from pharmacyos_erp.api.v1 import orders

		self.reserve_last_unit()
		frappe.set_user(self.api_user)
		try:
			with self.assertRaises(frappe.ValidationError):
				orders.create_order(
					f"REMED-RES2-{self.tag}", CODE, [{"item_code": self.item.name, "qty": 1}], {"id": "remed-res"}
				)
		finally:
			frappe.set_user("Administrator")

	def test_fulfilling_the_order_itself_is_allowed(self):
		from pharmacyos_erp.integration import cloud

		self.reserve_last_unit()
		so = frappe.get_doc("Sales Order", {"pharmacyos_order_id": f"REMED-RES-{self.tag}"})
		with patch.object(cloud, "_settings", return_value=frappe._dict(online_payment_mode="Cash")):
			cloud._fulfil(so)
		so.reload()
		self.assertEqual(flt(so.per_delivered), 100)

	def test_protection_can_be_switched_off(self):
		self.reserve_last_unit()
		set_settings(protect_online_reservations=0)
		si = submit_sale(self.item.name, 1, self.good)
		self.assertEqual(si.docstatus, 1)

	def test_unreserved_stock_sells_normally(self):
		si = submit_sale(self.item.name, 1)
		self.assertEqual(si.docstatus, 1)


# --------------------------------------------------------------------------- F-20 over HTTP


class TestConcurrentCounterSales(IntegrationTestCase):
	"""Two cashiers save and submit the last unit at the same moment: one sale, never a deadlock."""

	def admin_client(self):
		from frappe.tests.test_api import make_request

		client = get_test_client()
		response = make_request(client.post, ("/api/method/login",), {"json": {"usr": "Administrator", "pwd": "admin"}})
		self.assertEqual(response.status_code, 200)
		return client

	def test_last_unit_counter_race_never_deadlocks(self):
		from frappe.tests.test_api import ThreadWithReturnValue

		ensure_cash_mode()
		clients = [self.admin_client(), self.admin_client()]
		for attempt in range(4):
			tag = frappe.generate_hash(length=6).upper()
			item = make_medicine(f"REMED-RACE-{tag}")
			batch = make_batch(item.name, f"RACE-{tag}", 300)
			receive(item.name, batch, 1)
			frappe.db.commit()

			def sell(client, item_code=item.name, batch_no=batch):
				draft = client.post("/api/method/frappe.client.insert", json={"doc": sale_doc(item_code, 1, batch_no)})
				if draft.status_code != 200:
					return draft.status_code, draft.get_data(as_text=True)[:300]
				done = client.post("/api/method/frappe.client.submit", json={"doc": draft.json["message"]})
				return done.status_code, done.get_data(as_text=True)[:300]

			threads = [ThreadWithReturnValue(target=sell, args=(client,)) for client in clients]
			for thread in threads:
				thread.start()
			for thread in threads:
				thread.join()
			results = [thread._return for thread in threads]
			statuses = sorted(r[0] for r in results)
			self.assertNotIn(500, statuses, f"round {attempt}: {results}")
			self.assertEqual(statuses.count(200), 1, f"round {attempt}: {results}")
			frappe.db.rollback()  # fresh snapshot
			sold = frappe.get_all(
				"Sales Invoice Item", filters={"item_code": item.name, "docstatus": 1}, pluck="parent"
			)
			self.assertEqual(len(sold), 1)


# --------------------------------------------------------------------------- F-13


class TestConcurrentDuplicateOrder(IntegrationTestCase):
	def test_unique_violation_resolves_to_the_existing_order(self):
		"""Simulate the race: both requests passed the existence checks, one insert wins."""
		from pharmacyos_erp.api.v1 import orders

		ensure_api_branch()
		item = published_medicine("REMED-DUP-MED")
		batch = make_batch(item.name, "REMED-DUP-1", 300)
		receive(item.name, batch, 5)
		order_id = f"REMED-DUP-{frappe.generate_hash(length=6)}"
		args = (order_id, CODE, [{"item_code": item.name, "qty": 1}], {"id": "remed-dup"})
		first = orders.create_order(*args)
		frappe.db.commit()  # the losing request rolls back its own transaction
		calls = {"n": 0}
		real = orders._existing_order

		def racing_lookup(*a, **kw):  # both pre-insert checks ran before the winner committed
			calls["n"] += 1
			return None if calls["n"] <= 2 else real(*a, **kw)

		# ERPNext's duplicate customer-PO check reads the same snapshot, so it does not see the winner either
		with (
			patch.object(orders, "_existing_order", side_effect=racing_lookup),
			patch("erpnext.selling.doctype.sales_order.sales_order.SalesOrder.validate_po"),
		):
			second = orders.create_order(*args)
		self.assertFalse(second["created"])
		self.assertEqual(second["sales_order"], first["sales_order"])
		self.assertEqual(frappe.db.count("Sales Order", {"pharmacyos_order_id": order_id}), 1)


# --------------------------------------------------------------------------- F-08


class TestImmutableVersionedEvents(IntegrationTestCase):
	def setUp(self):
		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		from frappe.utils.password import set_encrypted_password

		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "s" * 40, "outbound_secret")
		ensure_api_branch()
		self.item = published_medicine(f"REMED-EVT-{frappe.generate_hash(length=5)}")
		self.batch = make_batch(self.item.name, f"EV-{frappe.generate_hash(length=5)}", 300)
		self.key = f"availability:{self.item.name}:{WAREHOUSE}"

	def tearDown(self):
		set_settings(enable_outbound_events=0, outbound_endpoint=None)

	def deliver(self, behaviour):
		from pharmacyos_erp.integration import outbox

		sent = []

		class Ok:
			def raise_for_status(self):
				return None

		def post(url, data, headers, timeout):
			sent.append((headers["X-PharmacyOS-Event-Id"], data))
			if behaviour == "lost":
				raise ConnectionError("response lost")
			return Ok()

		with patch("requests.post", side_effect=post):
			outbox.process_outbox()
		return sent

	def age(self):
		frappe.db.sql(
			"update `tabPharmacyOS Sync Event` set modified=%s where dedupe_key=%s",
			(add_days(now_datetime(), -1), self.key),
		)

	def test_lost_response_then_stock_change_then_retry(self):
		receive(self.item.name, self.batch, 5)
		first = self.deliver("lost")  # Cloud applied qty 5 but the response was lost
		ours = [s for s in first if json.loads(s[1])["data"].get("item_code") == self.item.name]
		self.assertEqual(json.loads(ours[0][1])["data"]["qty"], 5)
		receive(self.item.name, self.batch, 3)  # stock changes before the retry
		self.age()
		retried = [s for s in self.deliver("ok") if json.loads(s[1])["data"].get("item_code") == self.item.name]
		by_id = {}
		for event_id, body in retried:
			by_id.setdefault(event_id, []).append(json.loads(body))
		# the original id is re-sent with exactly the same state ...
		self.assertEqual(by_id[ours[0][0]][0]["data"]["qty"], 5)
		self.assertEqual(json.dumps(by_id[ours[0][0]][0], sort_keys=True), json.dumps(json.loads(ours[0][1]), sort_keys=True))
		# ... and the change travels as a new event with a later version
		newer = [p for eid, ps in by_id.items() if eid != ours[0][0] for p in ps]
		self.assertEqual(newer[-1]["data"]["qty"], 8)
		self.assertGreater(newer[-1]["computed_at"], json.loads(ours[0][1])["computed_at"])

	def test_unsent_events_absorb_new_changes(self):
		receive(self.item.name, self.batch, 2)
		receive(self.item.name, self.batch, 2)
		self.assertEqual(
			frappe.db.count("PharmacyOS Sync Event", {"dedupe_key": self.key, "status": "Pending"}), 1
		)

	def test_unreachable_events_are_revived_once_the_cloud_answers(self):
		from pharmacyos_erp.integration import outbox

		receive(self.item.name, self.batch, 1)
		self.deliver("lost")
		frappe.db.set_value(
			"PharmacyOS Sync Event", {"dedupe_key": self.key}, {"attempts": outbox.MAX_ATTEMPTS, "status": "Failed"}
		)
		rejected = frappe.get_doc(
			{
				"doctype": "PharmacyOS Sync Event",
				"event_type": "catalog.changed",
				"reference_doctype": "Item",
				"reference_name": self.item.name,
				"dedupe_key": f"catalog:{self.item.name}:rejected",
				"status": "Failed",
				"attempts": outbox.MAX_ATTEMPTS,
				"last_error": "400 Client Error: Bad Request",
			}
		).insert(ignore_permissions=True)
		outbox.queue_event("catalog.changed", "Item", self.item.name, f"catalog:{self.item.name}:probe")
		self.deliver("ok")
		self.assertEqual(frappe.db.get_value("PharmacyOS Sync Event", {"dedupe_key": self.key}, "attempts"), 0)
		self.assertEqual(frappe.db.get_value("PharmacyOS Sync Event", rejected.name, "status"), "Failed")


# --------------------------------------------------------------------------- F-14


class TestFirstRunIdempotency(IntegrationTestCase):
	def test_rerun_after_initialisation_changes_nothing(self):
		from pharmacyos_erp.setup.first_run import is_initialized, setup_pharmacy

		ensure_api_branch()
		set_settings(pharmacy_name="Remediation Pharmacy", pharmacy_name_ar="صيدلية")
		# an initialised pharmacy has finished ERPNext's setup; arrange it here rather than rely on how
		# the test site was prepared (CI-warmed sites have it, a freshly created site does not)
		completed = patch.object(frappe, "is_setup_complete", return_value=True)
		completed.start()
		self.addCleanup(completed.stop)
		self.assertTrue(is_initialized())
		owner = make_user("remed-owner@example.com", ["Pharmacy Owner"])
		update_password(owner, "Original-Pass-1")
		result = setup_pharmacy(
			pharmacy_name="Someone Else", owner_email=owner, owner_password="Hijacked-Pass-2"
		)
		self.assertEqual(result["status"], "already_initialized")
		self.assertEqual(frappe.db.get_single_value("PharmacyOS Settings", "pharmacy_name"), "Remediation Pharmacy")
		from frappe.utils.password import check_password

		self.assertEqual(check_password(owner, "Original-Pass-1"), owner)
		with self.assertRaises(frappe.AuthenticationError):
			check_password(owner, "Hijacked-Pass-2")
		self.assertNotIn("System Manager", frappe.get_roles(owner))

	def test_invalid_input_is_rejected_before_anything_is_created(self):
		from pharmacyos_erp.setup.first_run import _validate_inputs

		for args in (
			("", "owner@example.com", "Long-enough-1", "Main", "MAIN"),
			("Pharmacy", "not-an-email", "Long-enough-1", "Main", "MAIN"),
			("Pharmacy", "owner@example.com", "short", "Main", "MAIN"),
			("Pharmacy", "owner@example.com", "Long-enough-1", "", "MAIN"),
		):
			with self.assertRaises(frappe.ValidationError):
				_validate_inputs(*args)

	def test_owner_roles_are_the_pharmacy_owner_profile_plus_system_manager(self):
		from pharmacyos_erp.setup.first_run import owner_roles

		roles = set(owner_roles())
		self.assertIn("System Manager", roles)
		self.assertIn("Pharmacy Owner", roles)
		for unrelated in ("HR Manager", "Projects Manager", "Marketing Manager", "Fleet Manager", "Translator"):
			self.assertNotIn(unrelated, roles)


# --------------------------------------------------------------------------- F-19 / F-22


class TestSecureAddresses(IntegrationTestCase):
	def test_cloud_addresses_must_use_https(self):
		settings = frappe.get_single("PharmacyOS Settings")
		for field in ("cloud_base_url", "outbound_endpoint"):
			for bad in ("http://cloud.example.com", "ftp://cloud.example.com", "cloud.example.com"):
				settings.set(field, bad)
				with self.assertRaises(frappe.ValidationError, msg=f"{field}={bad}"):
					settings.validate()
			settings.set(field, "https://cloud.example.com")
			settings.validate()
			settings.set(field, None)

	def test_local_http_only_in_developer_mode(self):
		from pharmacyos_erp.pharmacyos.doctype.pharmacyos_settings.pharmacyos_settings import is_insecure_url

		with patch.dict(frappe.conf, {"developer_mode": 0}):
			self.assertTrue(is_insecure_url("http://127.0.0.1:8001"))
		with patch.dict(frappe.conf, {"developer_mode": 1}):
			self.assertFalse(is_insecure_url("http://127.0.0.1:8001"))
			self.assertTrue(is_insecure_url("http://cloud.example.com"))

	def test_requests_refuse_insecure_cloud_address(self):
		from pharmacyos_erp.integration import cloud

		with (
			patch.object(
				cloud, "_settings", return_value=frappe._dict(cloud_base_url="http://cloud.example.com", get_password=lambda *a, **k: "s" * 40)
			),
			patch("requests.request") as request,
		):
			with self.assertRaises(frappe.ValidationError):
				cloud.cloud_request("GET", "/integrations/erp/ping")
		request.assert_not_called()


class TestSessionPolicy(IntegrationTestCase):
	def test_counter_sessions_expire_after_a_shift_of_inactivity(self):
		from pharmacyos_erp.setup.install import configure_sessions

		before = frappe.db.get_single_value("System Settings", "session_expiry")
		try:
			configure_sessions()
			self.assertEqual(frappe.db.get_single_value("System Settings", "session_expiry"), "12:00")
		finally:
			frappe.db.set_single_value("System Settings", "session_expiry", before)
