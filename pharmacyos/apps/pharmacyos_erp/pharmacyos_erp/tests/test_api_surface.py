# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Every server method the PharmacyOS UI calls must be reachable over HTTP (whitelisted).

Regression guard: unit tests call functions directly, so a lost @frappe.whitelist() decorator once
broke the dashboard for real users while every test stayed green.
"""

import glob
import os
import re

import frappe
from frappe.tests import IntegrationTestCase


class TestApiSurface(IntegrationTestCase):
	def test_every_ui_method_is_whitelisted(self):
		app = frappe.get_app_path("pharmacyos_erp")
		sources = [
			f
			for f in glob.glob(os.path.join(app, "**", "*.js"), recursive=True)
			if "/dist/" not in f and "node_modules" not in f
		]
		methods = set()
		for path in sources:
			with open(path) as f:
				methods |= set(re.findall(r"[\"'](pharmacyos_erp\.[a-z0-9_.]+)[\"']", f.read()))
		self.assertGreater(len(methods), 10)
		missing = []
		for method in sorted(methods):
			try:
				fn = frappe.get_attr(method)
			except Exception:
				missing.append(f"{method} (not importable)")
				continue
			if fn not in frappe.whitelisted:
				missing.append(method)
		self.assertEqual(missing, [])
