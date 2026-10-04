# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""N-02 / N-03 / N-04: the staff and API permission matrix, over HTTP with real logins.

Every cell of `permission_matrix.EXPECTED` is a real REST / RPC request made as a user who holds
exactly one shipped role profile (fresh accounts per run), through Frappe's request handler — the
same routing, session and permission checks as a production server. Guest is a client with no login.

* N-02: owners, managers and inventory managers maintain medicine prices (Item Price, and "Add
  Medicine" with a selling rate); everyone who sells can read them; nobody else.
* N-03: Cashier, Pharmacist and the integration credential cannot create, submit or cancel Sales
  Orders, Delivery Notes or stock reservations through the REST API; counter sales and the whole POS
  shift still work; the integration account still places website orders through the PharmacyOS API.
* N-04: managers and the pharmacy accountant cancel batch-medicine sales, and the cancellation
  restores stock and batch quantity and reverses the GL; counter staff cannot cancel.

The same matrix runs against a live multi-worker server: `pharmacyos/dev/permission_check.py`.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_test_client, nowdate
from frappe.utils.password import update_password

from pharmacyos_erp.tests import permission_matrix
from pharmacyos_erp.tests.test_integration import CODE, ensure_api_branch
from pharmacyos_erp.tests.test_pos_shift import ensure_pos_profile
from pharmacyos_erp.tests.test_remediation import ensure_cash_mode
from pharmacyos_erp.tests.utils import COMPANY, CUSTOMER, WAREHOUSE, make_user, profile_roles

PASSWORD = "Matrix-Pass-123"


class Client:
	"""permission_matrix transport over Frappe's request handler (one cookie jar per user)."""

	def __init__(self, user=None, password=None):
		from frappe.tests.test_api import make_request

		self._request = make_request
		self.http = get_test_client()
		if user:
			response = make_request(
				self.http.post, ("/api/method/login",), {"json": {"usr": user, "pwd": password}}
			)
			assert response.status_code == 200, response.get_data(as_text=True)

	def _out(self, response):
		try:
			return response.status_code, response.json
		except Exception:
			return response.status_code, {"raw": response.get_data(as_text=True)[:300]}

	def post(self, method, data):
		return self._out(self._request(self.http.post, (f"/api/method/{method}",), {"json": data}))

	def get(self, method, params):
		return self._out(self._request(self.http.get, (f"/api/method/{method}",), {"query_string": params}))


class TestPermissionMatrixOverHTTP(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()
		ensure_api_branch()
		from pharmacyos_erp.setup.install import ensure_custom_permissions, ensure_role_profiles

		ensure_role_profiles()
		ensure_custom_permissions()
		tag = frappe.generate_hash(length=6).lower()
		cls.users, cls.counters = {}, {}
		for profile in permission_matrix.PROFILES:
			if profile == "Guest":
				continue
			email = make_user(
				f"matrix-{profile.lower().replace(' ', '-')}-{tag}@example.com", profile_roles(profile)
			)
			update_password(email, PASSWORD)
			frappe.defaults.set_user_default("Company", COMPANY, email)
			cls.users[profile] = email
			if profile != "PharmacyOS Integration":
				# one counter per seller and run: ERPNext allows one open shift per counter and per user
				cls.counters[profile] = (ensure_pos_profile(f"Matrix {tag} {profile}", [email]), email)
		frappe.db.commit()  # requests run on their own database connections

	def ctx(self):
		return {
			"company": COMPANY,
			"warehouse": WAREHOUSE,
			"branch_code": CODE,
			"customer": CUSTOMER,
			"currency": "INR",
			"cash_account": "Cash - _TC",
			"receivable_account": "Debtors - _TC",
			"income_account": "Sales - _TC",
			"price_list": "Standard Selling",
			"buying_price_list": "Standard Buying",
			"today": nowdate(),
			"item_group": "Products",
			"sale_extra": {"debit_to": "Debtors - _TC"},
			"row_extra": {
				"income_account": "Sales - _TC",
				"expense_account": "Cost of Goods Sold - _TC",
				"cost_center": "_Test Cost Center - _TC",
			},
			"dn_row_extra": {
				"expense_account": "Cost of Goods Sold - _TC",
				"cost_center": "_Test Cost Center - _TC",
			},
			"je_row_extra": {"cost_center": "_Test Cost Center - _TC"},
		}

	def test_every_role_gets_exactly_its_permissions(self):
		clients = {profile: Client(email, PASSWORD) for profile, email in self.users.items()}
		clients["Guest"] = Client()
		matrix = permission_matrix.Matrix(
			Client("Administrator", "admin"), clients, self.ctx(), self.counters
		)
		matrix.run()
		failures = matrix.failures()
		self.assertFalse(
			failures,
			"\n".join(
				[matrix.table(), ""]
				+ [
					f"{f['op']} as {f['profile']}: expected {f['expected']}, got {f['observed']} {f['detail']}"
					for f in failures
				]
			),
		)
		cancels = [r for r in matrix.results if r["op"] == "sales_invoice.cancel.reversal"]
		self.assertEqual(len(cancels), 3, "owner, manager and accountant each cancelled a batch sale")


class TestRoleModel(IntegrationTestCase):
	def test_counter_and_integration_profiles_hold_no_broad_erpnext_role(self):
		from pharmacyos_erp.setup.install import ensure_role_profiles

		ensure_role_profiles()
		broad = {
			"Sales User",
			"Sales Manager",
			"Accounts User",
			"Accounts Manager",
			"Stock User",
			"Stock Manager",
		}
		for profile in ("Cashier", "Pharmacist", "PharmacyOS Integration"):
			roles = {r.role for r in frappe.get_doc("Role Profile", profile).roles}
			self.assertFalse(roles & broad, f"{profile}: {roles & broad}")

	def test_upgrade_takes_sales_user_away_from_existing_counter_users(self):
		from unittest.mock import patch

		from pharmacyos_erp.setup.install import ensure_role_profiles

		profile = frappe.get_doc("Role Profile", "Pharmacist")
		profile.append("roles", {"role": "Sales User"})
		profile.flags.ignore_permissions = True
		profile.save()
		user = make_user("matrix-upgraded-pharmacist@example.com", [])
		doc = frappe.get_doc("User", user)
		doc.set("role_profiles", [{"role_profile": "Pharmacist"}])
		doc.save(ignore_permissions=True)
		self.assertIn("Sales User", frappe.get_roles(user))
		with patch.object(frappe, "in_test", False):  # as in migrate
			ensure_role_profiles()
		frappe.clear_cache(user=user)
		self.assertNotIn("Sales User", frappe.get_roles(user))
		self.assertIn("Pharmacist", frappe.get_roles(user))

	def test_permission_rows_are_declarative(self):
		"""A right removed from the table is taken away on the next migrate (not only added ones kept)."""
		from pharmacyos_erp.setup.install import CUSTOM_PERMISSIONS, ensure_custom_permissions

		ensure_custom_permissions()
		name = frappe.db.get_value(
			"Custom DocPerm", {"parent": "Item Price", "role": "Cashier", "permlevel": 0}
		)
		frappe.db.set_value("Custom DocPerm", name, {"write": 1, "delete": 1})
		ensure_custom_permissions()
		row = frappe.db.get_value("Custom DocPerm", name, ["read", "write", "delete"], as_dict=True)
		self.assertEqual((row.read, row.write, row.delete), (1, 0, 0))
		self.assertEqual(CUSTOM_PERMISSIONS["Cashier"]["Item Price"], ("read", "select"))

	def test_integration_order_is_owned_by_the_integration_account(self):
		from pharmacyos_erp.api.v1 import orders
		from pharmacyos_erp.tests.test_integration import published_medicine
		from pharmacyos_erp.tests.utils import make_batch, receive

		ensure_api_branch()
		item = published_medicine(f"MATRIX-OWN-{frappe.generate_hash(length=5)}")
		batch = make_batch(item.name, f"MO-{item.name}", 300)
		receive(item.name, batch, 2)
		api_user = make_user("matrix-owner-check@example.test", profile_roles("PharmacyOS Integration"))
		frappe.set_user(api_user)
		try:
			result = orders.create_order(
				f"MATRIX-OWN-{frappe.generate_hash(length=6)}",
				CODE,
				[{"item_code": item.name, "qty": 1}],
				{"id": "matrix-own"},
			)
			# the endpoint's elevated step never leaks into the caller's session
			self.assertEqual(frappe.session.user, api_user)
			self.assertFalse(frappe.has_permission("Sales Order", "create"))
			self.assertFalse(frappe.has_permission("Item", "read"))
		finally:
			frappe.set_user("Administrator")
		so = frappe.get_doc("Sales Order", result["sales_order"])
		self.assertEqual((so.owner, so.docstatus), (api_user, 1))
