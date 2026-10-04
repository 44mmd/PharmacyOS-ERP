# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Counter staff never receive purchase prices, valuation or stock values (round-3 red-team).

The probe (`cost_exposure.py`) seeds a medicine with distinctive costs and asks for them through every
route a user has — document APIs, list/report views, form loads, single values, aggregates, filters,
ERPNext's cost helpers and query reports — as each profile, over Frappe's request handler with real
logins. Before the fix a Cashier received them through 25 routes, a Pharmacist through 26 and the
integration account through 2. The same probe runs against gunicorn:
`pharmacyos/dev/permission_check.py --cost`.

Counter work is unchanged (the permission matrix covers the POS shift, sales and returns): this module
also proves that a cashier's sale still records its costs correctly — the field-level rule only hides
them from the cashier; the server computes and stores them as before.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt
from frappe.utils.password import update_password

from pharmacyos_erp.tests import cost_exposure, test_permissions_matrix
from pharmacyos_erp.tests.test_permissions_matrix import PASSWORD, Client
from pharmacyos_erp.tests.test_remediation import ensure_cash_mode
from pharmacyos_erp.tests.utils import COMPANY, make_user, profile_roles

NO_COST = ("Cashier", "Pharmacist", "PharmacyOS Integration")
CONTROL = ("Pharmacy Owner", "Inventory Manager")


class TestCostExposure(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()
		from pharmacyos_erp.setup.install import ensure_structure

		ensure_structure()
		cls.users = {}  # fixed accounts, re-used across runs (Frappe throttles bulk user creation)
		for profile in (*NO_COST, *CONTROL):
			email = make_user(f"cost-{profile.lower().replace(' ', '-')}@example.com", profile_roles(profile))
			update_password(email, PASSWORD)
			frappe.defaults.set_user_default("Company", COMPANY, email)
			cls.users[profile] = email
		frappe.db.commit()

	def ctx(self):
		# (the class is referenced through its module so that it is not collected here a second time)
		return test_permissions_matrix.TestPermissionMatrixOverHTTP.ctx(self)

	def test_no_cost_data_for_counter_staff_through_any_route(self):
		frappe.db.commit()  # the probe's requests run on their own database connections
		clients = {p: Client(u, PASSWORD) for p, u in self.users.items()}
		probe = cost_exposure.CostProbe(Client("Administrator", "admin"), clients, self.ctx())
		probe.run()
		for profile in NO_COST:
			self.assertFalse(probe.leaks(profile), f"{profile} received cost data:\n{probe.table()}")
		for profile in CONTROL:  # the probe can see costs where they are allowed — it proves something
			self.assertTrue(probe.leaks(profile), f"control {profile} saw nothing:\n{probe.table()}")

	def test_counter_staff_still_see_selling_prices_and_availability(self):
		from pharmacyos_erp.tests.test_integration import published_medicine
		from pharmacyos_erp.tests.utils import WAREHOUSE, make_batch, receive

		item = published_medicine(f"COST-SELL-{frappe.generate_hash(length=5).upper()}").name
		receive(item, make_batch(item, f"CS-{item}", 300), 7, rate=4321)
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": item,
				"price_list": "Standard Buying",
				"price_list_rate": 3999,
			}
		).insert()
		for profile in ("Cashier", "Pharmacist"):
			frappe.set_user(self.users[profile])
			try:
				prices = frappe.get_list(
					"Item Price", filters={"item_code": item}, fields=["price_list", "price_list_rate"]
				)
				self.assertEqual(
					[(p.price_list, flt(p.price_list_rate)) for p in prices], [("Standard Selling", 1000)]
				)
				stock = frappe.get_list(
					"Bin",
					filters={"item_code": item, "warehouse": WAREHOUSE},
					fields=["actual_qty", "projected_qty"],
				)
				self.assertEqual(flt(stock[0].actual_qty), 7)
				self.assertTrue(
					frappe.get_list("Item", filters={"name": item}, fields=["item_name", "stock_uom"])
				)
			finally:
				frappe.set_user("Administrator")

	def test_a_cashiers_sale_still_records_its_costs(self):
		"""Hidden from the cashier, computed and stored by the server exactly as before."""
		from pharmacyos_erp.tests.test_return_concurrency import submitted_doc
		from pharmacyos_erp.tests.utils import make_batch, make_medicine, receive

		item = make_medicine(f"COST-SALE-{frappe.generate_hash(length=5).upper()}").name
		batch = make_batch(item, f"CSA-{item}", 300)
		receive(item, batch, 5, rate=2468)
		doc = submitted_doc(item, 2, batch)
		doc["docstatus"] = 0
		frappe.set_user(self.users["Cashier"])
		try:
			sale = frappe.get_doc(doc).insert()
			sale.items[0].incoming_rate = 1  # a value sent by the client for a hidden field is discarded
			sale.submit()
			name = sale.name
		finally:
			frappe.set_user("Administrator")
		sale = frappe.get_doc("Sales Invoice", name)
		self.assertEqual(flt(sale.items[0].incoming_rate), 2468)
		sle = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": name, "is_cancelled": 0},
			fields=["stock_value_difference", "actual_qty"],
		)
		self.assertEqual((flt(sle[0].actual_qty), flt(sle[0].stock_value_difference)), (-2, -4936))
		bundle = frappe.get_doc("Serial and Batch Bundle", sale.items[0].serial_and_batch_bundle)
		self.assertEqual(flt(bundle.avg_rate), 2468)
		import erpnext

		if erpnext.is_perpetual_inventory_enabled(COMPANY):  # the cost of goods sold reaches the GL
			gl = frappe.get_all(
				"GL Entry", filters={"voucher_no": name, "is_cancelled": 0}, fields=["debit", "credit"]
			)
			self.assertAlmostEqual(sum(flt(g.debit) for g in gl), sum(flt(g.credit) for g in gl))
			self.assertTrue(any(abs(flt(g.credit) - 4936) < 0.01 for g in gl), gl)

	def test_cost_access_follows_the_role_profiles(self):
		from pharmacyos_erp.pharmacy.cost_privacy import can_see_costs

		for profile in NO_COST:
			self.assertFalse(can_see_costs(self.users[profile]), profile)
		for profile in CONTROL:
			self.assertTrue(can_see_costs(self.users[profile]), profile)
		accountant = make_user("cost-accountant@example.com", profile_roles("Pharmacy Accountant"))
		self.assertTrue(can_see_costs(accountant))

	def test_setup_is_idempotent_and_declarative(self):
		from pharmacyos_erp.pharmacy.cost_privacy import COST_LEVEL, ensure_cost_privacy

		ensure_cost_privacy()
		before = frappe.db.count("Custom DocPerm", {"permlevel": COST_LEVEL})
		# a cost row someone gave the cashier is taken away again on the next migrate
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": "Item",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": "Cashier",
				"permlevel": COST_LEVEL,
				"read": 1,
			}
		).db_insert()
		ensure_cost_privacy()
		self.assertEqual(frappe.db.count("Custom DocPerm", {"permlevel": COST_LEVEL}), before)
		self.assertFalse(
			frappe.db.exists("Custom DocPerm", {"parent": "Item", "role": "Cashier", "permlevel": COST_LEVEL})
		)
		self.assertEqual(frappe.get_meta("Bin").get_field("stock_value").permlevel, COST_LEVEL)

	def test_pharmacy_pages_show_quantities_but_no_cost_aggregates(self):
		"""Dashboard, batches & expiry, expiry intelligence, inventory health: totals at valuation too."""
		from pharmacyos_erp.pharmacy import dashboard, expiry, inventory

		def pages():
			return {
				"dashboard": dashboard.get_dashboard(),
				"batches": expiry.get_batches(),
				"intelligence": expiry.get_expiry_intelligence(horizon=90),
				"health": inventory.get_inventory_health(),
			}

		def numbers(payload, keys=("value", "inventory_value", "total_value", "risk_value", "expired_value")):
			found = []
			if isinstance(payload, dict):
				for k, v in payload.items():
					if k in keys and isinstance(v, int | float):
						found.append(v)
					found += numbers(v, keys)
			elif isinstance(payload, list):
				for v in payload:
					found += numbers(v, keys)
			return found

		for profile in ("Cashier", "Pharmacist"):
			frappe.set_user(self.users[profile])
			try:
				result = pages()
			finally:
				frappe.set_user("Administrator")
			for name, payload in result.items():
				self.assertIs(payload["costs_visible"], False, name)
				self.assertEqual(numbers(payload), [], f"{profile} {name}: cost figures sent")
			profit = result["dashboard"]["gross_profit"]  # no access at all, or blanked
			self.assertTrue(profit is None or profit["value"] is None, profit)
			self.assertTrue(result["health"]["counts"], "quantities and states are still there")
		frappe.set_user(self.users["Pharmacy Owner"])
		try:
			result = pages()
		finally:
			frappe.set_user("Administrator")
		self.assertIs(result["health"]["costs_visible"], True)
		self.assertIsInstance(result["health"]["inventory_value"], float)
