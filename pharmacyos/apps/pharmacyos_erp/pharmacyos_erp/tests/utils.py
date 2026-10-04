# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Test helpers. Every stock movement goes through submitted ERPNext documents."""

import frappe
from frappe.utils import add_days, nowdate


def ensure_test_masters() -> None:
	"""ERPNext's standard test masters (_Test Company, its accounts and warehouses, _Test Customer,
	item groups, UOMs, Mode of Payment Cash, price lists …), which these tests build on.

	ERPNext v16 creates them when `erpnext.tests.utils` is imported (BootStrapTestData, idempotent);
	upstream CI warms them with a separate run before any suite. The PharmacyOS tests must not depend on
	that warm-up — nor on another test having run first — so every test module requests them itself.
	Only ever in a test run, never on a real site.
	"""
	if not frappe.in_test:
		return
	import erpnext.tests.utils  # the import creates the masters


ensure_test_masters()

COMPANY = "_Test Company"
WAREHOUSE = "_Test Warehouse - _TC"
WAREHOUSE_2 = "_Test Warehouse 1 - _TC"
CUSTOMER = "_Test Customer"


def make_medicine(code, **kwargs):
	if frappe.db.exists("Item", code):
		return frappe.get_doc("Item", code)
	from pharmacyos_erp.pharmacy.medicine import create_medicine

	args = {
		"item_code": code,
		"item_name": kwargs.pop("item_name", f"Test {code}"),
		"item_group": "Products",
		"stock_uom": "Nos",
		"standard_rate": 1000,
	}
	args.update(kwargs)
	return frappe.get_doc("Item", create_medicine(**args))


def make_batch(item_code, batch_id, expiry_days):
	existing = frappe.db.get_value("Batch", {"batch_id": batch_id, "item": item_code})
	if existing:
		return existing
	return (
		frappe.get_doc(
			{
				"doctype": "Batch",
				"batch_id": batch_id,
				"item": item_code,
				"expiry_date": add_days(nowdate(), expiry_days),
			}
		)
		.insert()
		.name
	)


def receive(item_code, batch, qty, warehouse=WAREHOUSE, rate=100, posting_date=None):
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": COMPANY,
			"set_posting_time": 1 if posting_date else 0,
			"posting_date": posting_date or nowdate(),
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"t_warehouse": warehouse,
					"basic_rate": rate,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
				}
			],
		}
	)
	se.insert()
	se.submit()
	return se


def make_sales_invoice(item_code, batch, qty, warehouse=WAREHOUSE, submit=False):
	si = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": COMPANY,
			"customer": CUSTOMER,
			"update_stock": 1,
			"debit_to": "Debtors - _TC",
			"currency": "INR",
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"rate": 1000,
					"warehouse": warehouse,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
					"income_account": "Sales - _TC",
					"expense_account": "Cost of Goods Sold - _TC",
					"cost_center": "_Test Cost Center - _TC",
				}
			],
		}
	)
	si.insert()
	if submit:
		si.submit()
	return si


def set_settings(**values):
	frappe.db.set_single_value("PharmacyOS Settings", values)
	frappe.clear_document_cache("PharmacyOS Settings", "PharmacyOS Settings")


def profile_roles(profile: str) -> list[str]:
	"""Roles of a shipped PharmacyOS role profile (tests use the real profiles, never hand-made lists)."""
	from pharmacyos_erp.setup.install import ROLE_PROFILES

	return list(ROLE_PROFILES[profile])


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
