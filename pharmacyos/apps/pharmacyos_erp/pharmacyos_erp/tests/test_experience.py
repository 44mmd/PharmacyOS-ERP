# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Stage D: sign-in page, website footer, print formats (branding surfaces)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.website.serve import get_response_content

from pharmacyos_erp.tests.utils import make_batch, make_medicine, make_sales_invoice, receive


class TestExperience(IntegrationTestCase):
	def test_login_page_wraps_frappe_forms(self):
		from frappe.utils import set_request

		frappe.set_user("Guest")
		try:
			set_request(method="GET", path="/login")  # Frappe's login context reads the request
			html = get_response_content("login")
		finally:
			frappe.set_user("Administrator")
		self.assertIn("pos-login", html)
		self.assertIn("PharmacyOS ERP", html)
		self.assertIn("HALF", html)
		# Frappe's own form markup and script are still rendered (upgrade-safe wrapper)
		for marker in ('id="login_email"', 'id="login_password"', "form-login", "login.js"):
			self.assertIn(marker, html)
		self.assertIn("/attribution", html)  # open-source notices remain reachable
		self.assertNotIn("frappe.io/erpnext?source=website_footer", html)

	def test_print_formats_render_batch_and_identity(self):
		item = make_medicine("POS-TEST-PRINT", item_name="Print Test Medicine")
		batch = make_batch(item.name, "PRN-01", 300)
		receive(item.name, batch, 5)
		si = make_sales_invoice(item.name, batch, 1, submit=True)
		for fmt in ("PharmacyOS Invoice", "PharmacyOS Receipt"):
			html = frappe.get_print("Sales Invoice", si.name, print_format=fmt)
			self.assertIn("PRN-01", html, fmt)  # human batch number, never the hash
			self.assertNotIn(batch if batch != "PRN-01" else "§", html, fmt)
			self.assertIn("Print Test Medicine", html, fmt)
