# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Round 5 (B-1): every value of `is_consolidated` sent by a client is classified strictly.

Reproduced on the round-4 candidate (independent re-validation): "true", "abc", 0.5, [1] and {} passed
the `cint`-based check (cint gave 0) while ERPNext treated them as set — an uncontrolled HTTP 500
(`TypeError` in `calculate_net_total`). These tests send the whole input matrix through every write
path (document API, REST, frappe.client.insert, desk savedocs, update of a draft), as a Cashier and as
a Pharmacy Manager, and prove that a refused request leaves nothing behind: no invoice, GL entry,
stock ledger entry, batch movement, return-ledger change or outbox event.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.test_api import make_request
from frappe.utils import flt, get_test_client

from pharmacyos_erp.pharmacy.consolidation import ConsolidatedFlagError, consolidating, flag_state
from pharmacyos_erp.tests.test_return_integrity_round4 import ReturnCase, doc, post
from pharmacyos_erp.tests.utils import COMPANY, CUSTOMER, WAREHOUSE, make_user, profile_roles, set_settings

ABSENT = object()

# value → expected classification
MATRIX = [
	(ABSENT, "unset"),
	(None, "unset"),
	(False, "unset"),
	(0, "unset"),
	(0.0, "unset"),
	("0", "unset"),
	("", "unset"),
	(True, "set"),
	(1, "set"),
	(1.0, "set"),
	("1", "set"),
	("true", "invalid"),
	("false", "invalid"),
	("True", "invalid"),
	("yes", "invalid"),
	("no", "invalid"),
	("abc", "invalid"),
	(" ", "invalid"),
	("\t\n", "invalid"),
	(" 1", "invalid"),
	("0.0", "invalid"),
	("1.0", "invalid"),
	(0.5, "invalid"),
	(-0.5, "invalid"),
	(0.0001, "invalid"),
	(-1, "invalid"),
	(2, "invalid"),
	(10**12, "invalid"),
	(float("nan"), "invalid"),
	(float("inf"), "invalid"),
	([], "invalid"),
	([1], "invalid"),
	([0], "invalid"),
	({}, "invalid"),
	({"a": 1}, "invalid"),
	({"is_consolidated": 1}, "invalid"),
	([[1]], "invalid"),
	([{"a": [1]}], "invalid"),
]

# JSON can carry everything except NaN / infinity
HTTP_MATRIX = [(v, s) for v, s in MATRIX if not (isinstance(v, float) and (v != v or v == float("inf")))]


def residue(item, sale=None) -> dict:
	"""Everything a write could leave behind for this medicine (and a sale's return ledger)."""
	invoices = frappe.get_all("Sales Invoice Item", filters={"item_code": item}, pluck="parent")
	return {
		"invoices": sorted(set(invoices)),
		"gl": frappe.db.count("GL Entry", {"voucher_no": ["in", invoices or [""]]}),
		"sle": frappe.db.count("Stock Ledger Entry", {"item_code": item}),
		"bin": flt(frappe.db.get_value("Bin", {"item_code": item, "warehouse": WAREHOUSE}, "actual_qty")),
		"bundles": frappe.db.count("Serial and Batch Bundle", {"item_code": item}),
		"outbox": frappe.db.count("PharmacyOS Sync Event"),
		"ledger": frappe.db.get_value("Sales Invoice", sale, "pharma_return_ledger") if sale else None,
	}


def with_flag(d: dict, value) -> dict:
	d = dict(d)
	if value is ABSENT:
		d.pop("is_consolidated", None)
	else:
		d["is_consolidated"] = value
	return d


class TestFlagClassification(IntegrationTestCase):
	def test_every_value_is_classified_deterministically(self):
		for value, expected in MATRIX:
			if value is ABSENT:
				continue
			with self.subTest(value=repr(value)):
				self.assertEqual(flag_state(value), expected)
				self.assertEqual(flag_state(value), expected, "same answer twice")


class TestFlagThroughTheDocumentApi(ReturnCase):
	def setUp(self):
		# outbound events on, so a write that slipped through would also leave an outbox event
		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		self.item, self.batch = self.medicine(20)
		self.sale = post(doc(self.item, 4, self.batch))

	def tearDown(self):
		set_settings(enable_outbound_events=0)

	def attempt(self, d, submit):
		return frappe.get_doc({**d, "docstatus": 1} if submit else d).insert()

	def test_matrix_on_sales_and_returns_as_administrator_and_cashier(self):
		cashier = make_user("r5-flag-cashier@example.com", profile_roles("Cashier"))
		frappe.defaults.set_user_default("Company", COMPANY, cashier)
		for user in ("Administrator", cashier):
			frappe.set_user(user)
			try:
				for value, expected in MATRIX:
					for kind in ("sale", "return"):
						base = (
							doc(self.item, 1, self.batch)
							if kind == "sale"
							else doc(self.item, 1, self.batch, return_against=self.sale.name)
						)
						with self.subTest(user=user, kind=kind, value=repr(value)):
							self.check(with_flag(base, value), expected)
			finally:
				frappe.set_user("Administrator")

	def check(self, d, expected):
		before = residue(self.item, self.sale.name)
		if expected == "unset":
			si = self.attempt(d, submit=False)  # a draft is enough: the flag is normalised on save
			self.assertEqual(si.is_consolidated, 0)
			self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, "is_consolidated"), 0)
			si.delete(ignore_permissions=True)
			self.assertEqual(residue(self.item, self.sale.name), before)
			return
		for submit in (False, True):
			with self.assertRaises(ConsolidatedFlagError):
				self.attempt(d, submit)
			self.assertEqual(residue(self.item, self.sale.name), before, "a refused write left a trace")

	def test_unset_values_still_post_a_normal_sale_and_return(self):
		for value in (ABSENT, None, False, 0, 0.0, "0", ""):
			with self.subTest(value=repr(value)):
				si = post(with_flag(doc(self.item, 1, self.batch), value))
				self.assertEqual((si.docstatus, si.is_consolidated), (1, 0))
				ret = post(with_flag(doc(self.item, 1, self.batch, return_against=si.name), value))
				self.assertEqual((ret.docstatus, ret.is_consolidated), (1, 0))

	def test_inside_pos_consolidation_only_0_or_1_are_accepted(self):
		with consolidating():
			for value in ("true", 0.5, [1], {}, 2, " "):
				with self.subTest(value=repr(value)):
					with self.assertRaises(ConsolidatedFlagError):
						frappe.get_doc(doc(self.item, 1, self.batch, is_consolidated=value)).insert()

	def test_updating_a_draft_with_any_set_or_invalid_value_is_refused(self):
		draft = frappe.get_doc(doc(self.item, 1, self.batch)).insert()
		for value, expected in MATRIX:
			if expected == "unset" or value is ABSENT:
				continue
			with self.subTest(value=repr(value)):
				d = frappe.get_doc("Sales Invoice", draft.name)
				d.is_consolidated = value
				with self.assertRaises(ConsolidatedFlagError):
					d.save()
				self.assertEqual(frappe.db.get_value("Sales Invoice", draft.name, "is_consolidated"), 0)


class TestFlagOverHttp(ReturnCase):
	"""Every write path a client can reach, for both a Cashier and a Pharmacy Manager."""

	PASSWORD = "R5-flag-pass-123456"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from frappe.utils.password import update_password

		cls.users = {}
		for profile in ("Cashier", "Pharmacy Manager"):
			email = f"r5-flag-{profile.lower().replace(' ', '-')}@example.com"
			make_user(email, profile_roles(profile))
			update_password(email, cls.PASSWORD)
			frappe.defaults.set_user_default("Company", COMPANY, email)
			cls.users[profile] = email
		frappe.db.commit()

	def setUp(self):
		set_settings(enable_outbound_events=1, outbound_endpoint="https://pharmacyos.invalid/hook")
		self.item, self.batch = self.medicine(20)
		self.sale = post(doc(self.item, 4, self.batch))
		self.draft = frappe.get_doc(doc(self.item, 1, self.batch)).insert()
		frappe.db.commit()

	def tearDown(self):
		set_settings(enable_outbound_events=0)
		frappe.db.commit()

	def client(self, profile):
		client = get_test_client()
		response = make_request(
			client.post, ("/api/method/login",), {"json": {"usr": self.users[profile], "pwd": self.PASSWORD}}
		)
		self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:200])
		return client

	def paths(self, value):
		"""(name, method, url, request kwargs) for every way a client can write a Sales Invoice."""
		sale = with_flag({**doc(self.item, 1, self.batch), "docstatus": 1}, value)
		ret = with_flag(
			{**doc(self.item, 1, self.batch, return_against=self.sale.name), "docstatus": 1}, value
		)
		draft = json.loads(frappe.as_json(frappe.get_doc("Sales Invoice", self.draft.name).as_dict()))
		draft = with_flag(draft, value)
		new = with_flag(doc(self.item, 1, self.batch), value)

		def desk(d, action):
			return {
				"data": {
					"doc": frappe.as_json({**d, "__islocal": 1, "name": "new-sales-invoice-r5"}),
					"action": action,
				}
			}

		set_value = {
			"doctype": "Sales Invoice",
			"name": self.draft.name,
			"fieldname": "is_consolidated",
			"value": value,
		}
		return [
			("rest insert", "post", "/api/resource/Sales Invoice", {"json": new}),
			("rest insert submitted", "post", "/api/resource/Sales Invoice", {"json": sale}),
			("frappe.client.insert", "post", "/api/method/frappe.client.insert", {"json": {"doc": sale}}),
			(
				"frappe.client.insert return",
				"post",
				"/api/method/frappe.client.insert",
				{"json": {"doc": ret}},
			),
			("savedocs Save", "post", "/api/method/frappe.desk.form.save.savedocs", desk(new, "Save")),
			("savedocs Submit", "post", "/api/method/frappe.desk.form.save.savedocs", desk(sale, "Submit")),
			(
				"rest update draft",
				"put",
				f"/api/resource/Sales Invoice/{self.draft.name}",
				{"json": {"is_consolidated": value}},
			),
			("frappe.client.save draft", "post", "/api/method/frappe.client.save", {"json": {"doc": draft}}),
			(
				"frappe.client.set_value draft",
				"post",
				"/api/method/frappe.client.set_value",
				{"json": set_value},
			),
		]

	def test_matrix_over_every_write_path(self):
		failures = []
		for profile in ("Cashier", "Pharmacy Manager"):
			client = self.client(profile)
			for value, expected in HTTP_MATRIX:
				if expected == "unset" or value is ABSENT:
					continue
				for name, method, url, kwargs in self.paths(value):
					frappe.db.rollback()
					before = residue(self.item, self.sale.name)
					response = make_request(getattr(client, method), (url,), kwargs)
					frappe.db.rollback()
					body = response.get_data(as_text=True)
					context = f"{profile} / {name} / {value!r}: {response.status_code} {body[:200]}"
					if response.status_code >= 500:
						failures.append("5xx " + context)
					elif response.status_code < 400:
						# frappe.client.set_value (v16) replaces a falsy value ([] / {}) with "": the
						# flag is then "not set" and stays 0 (checked below), nothing is accepted
						if not (method == "post" and url.endswith("set_value") and not value):
							failures.append("accepted " + context)
					elif "ConsolidatedFlagError" not in body and "UpdateAfterSubmitError" not in body:
						# the guard is the only acceptable refusal for a write the user may make
						failures.append("other refusal " + context)
					if residue(self.item, self.sale.name) != before:
						failures.append("residue " + context)
					if frappe.db.get_value("Sales Invoice", self.draft.name, "is_consolidated") != 0:
						failures.append("draft changed " + context)
		self.assertFalse(failures, "\n".join(failures[:40]))

	def test_unset_values_are_accepted_over_http(self):
		client = self.client("Cashier")
		for value in (0, "0", "", False, None):
			with self.subTest(value=repr(value)):
				response = make_request(
					client.post,
					("/api/method/frappe.client.insert",),
					{
						"json": {
							"doc": {**doc(self.item, 1, self.batch), "is_consolidated": value, "docstatus": 1}
						}
					},
				)
				self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
				self.assertEqual(response.json["message"]["is_consolidated"], 0)
