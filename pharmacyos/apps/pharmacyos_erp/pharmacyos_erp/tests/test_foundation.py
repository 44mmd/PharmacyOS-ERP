# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Stage A: install structure, configuration, branding, IQD and Arabic foundations."""

import frappe
from frappe.tests import IntegrationTestCase

from pharmacyos_erp.branding import PRODUCT, UPSTREAM, get_brand
from pharmacyos_erp.setup.custom_fields import CUSTOM_FIELDS
from pharmacyos_erp.setup.install import ROLE_PROFILES, ROLES, configure_currency, ensure_structure
from pharmacyos_erp.utils.money import format_money


class TestFoundation(IntegrationTestCase):
	def test_structure_is_idempotent(self):
		ensure_structure()
		ensure_structure()
		for role in ROLES:
			self.assertTrue(frappe.db.exists("Role", role), role)

	def test_role_profiles_never_grant_system_manager(self):
		for profile in ROLE_PROFILES:
			roles = {r.role for r in frappe.get_doc("Role Profile", profile).roles}
			self.assertNotIn("System Manager", roles, profile)
			self.assertNotIn("Administrator", roles, profile)

	def test_cashier_profile_is_minimal(self):
		roles = {r.role for r in frappe.get_doc("Role Profile", "Cashier").roles}
		self.assertEqual(roles, {"Cashier", "Sales User", "Accounts User"})

	def test_custom_fields_exist_and_are_prefixed(self):
		for doctype, fields in CUSTOM_FIELDS.items():
			meta = frappe.get_meta(doctype)
			for field in fields:
				self.assertTrue(field["fieldname"].startswith(("pharma_", "pharmacyos_")), field["fieldname"])
				self.assertTrue(meta.has_field(field["fieldname"]), f"{doctype}.{field['fieldname']}")

	def test_medicine_flag_defaults_off(self):
		"""Existing ERPNext items are unaffected unless explicitly marked as medicines."""
		item = frappe.new_doc("Item")
		self.assertEqual(int(item.pharma_is_medicine or 0), 0)

	def test_item_search_includes_arabic_and_generic_names(self):
		search_fields = frappe.get_meta("Item").search_fields
		self.assertIn("pharma_name_ar", search_fields)
		self.assertIn("pharma_generic_name", search_fields)

	def test_iqd_presentation_keeps_precision(self):
		configure_currency()
		iqd = frappe.get_doc("Currency", "IQD")
		self.assertEqual(iqd.enabled, 1)
		self.assertEqual(iqd.symbol, "IQD")
		self.assertEqual(iqd.symbol_on_right, 1)
		# presentation hides an all-zero fraction but never a real one
		self.assertEqual(format_money(25000, "IQD", lang="en"), "25,000 IQD")
		self.assertTrue(format_money(333.333, "IQD", lang="en").startswith("333.33"))
		self.assertEqual(format_money(25000, "IQD", lang="ar"), "25,000 د.ع")

	def test_arabic_pharmacy_terminology_overrides(self):
		frappe.local.lang = "ar"
		try:
			frappe.translate.clear_cache()
			self.assertEqual(frappe._("Batch", lang="ar"), "الوجبة")
			self.assertEqual(frappe._("Batch No", lang="ar"), "رقم الوجبة")
			self.assertEqual(frappe._("IQD", lang="ar"), "د.ع")
		finally:
			frappe.local.lang = "en"

	def test_brand_keeps_upstream_attribution(self):
		brand = get_brand()
		self.assertEqual(brand["product_name"], "PharmacyOS ERP")
		self.assertEqual(brand["attribution"], "Designed & Developed by HALF")
		names = {u["name"] for u in UPSTREAM}
		self.assertEqual(names, {"ERPNext", "Frappe Framework"})
		self.assertIn("GNU General Public License v3", {u["license"] for u in UPSTREAM})
		self.assertNotIn("ERPNext", PRODUCT["product_name"])

	def test_app_permission_requires_pharmacy_role(self):
		from pharmacyos_erp.permissions import has_app_permission

		user = make_user("pos-noroles@example.test", [])
		frappe.set_user(user)
		try:
			self.assertFalse(has_app_permission())
		finally:
			frappe.set_user("Administrator")
		frappe.get_doc("User", user).add_roles("Pharmacist")
		frappe.set_user(user)
		try:
			self.assertTrue(has_app_permission())
		finally:
			frappe.set_user("Administrator")


def make_user(email, roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.remove_roles(*[r.role for r in user.roles])
	if roles:
		user.add_roles(*roles)
	return email


class TestNavigation(IntegrationTestCase):
	def test_navigation_targets_exist_and_are_owned_once(self):
		from collections import Counter

		from pharmacyos_erp.setup.navigation import NAVIGATION

		owned = Counter()
		for name, _title, _icon, _items in NAVIGATION:
			sidebar = frappe.get_doc("Sidebar", name)
			self.assertEqual(sidebar.app, "pharmacyos_erp")
			for row in sidebar.items:
				if row.type != "Link":
					continue
				self.assertTrue(
					frappe.db.exists(row.link_type, row.link_to), f"{row.link_type} {row.link_to}"
				)
				if row.is_default_module:
					owned[(row.link_type, row.link_to)] += 1
		self.assertTrue(owned)
		self.assertEqual(max(owned.values()), 1)

	def test_dock_lists_every_section(self):
		from pharmacyos_erp.setup.navigation import NAVIGATION

		dock = frappe.get_doc("Dock", "pharmacyos_erp")
		self.assertEqual([r.link_to for r in dock.items], [n[0] for n in NAVIGATION])
