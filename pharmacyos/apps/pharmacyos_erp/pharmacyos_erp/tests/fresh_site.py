# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Fresh-install acceptance check (Flow A), run against a brand-new site with NO background worker:

	pharmacyos/dev/fresh-site-check.sh <site>

It runs first-run setup, then immediately receives stock with a Purchase Receipt and sells it at the
counter, and asserts every Branch accounting-dimension column exists and the health check is green.
It also re-runs first-run setup to prove it changes nothing once initialised.

Not a unit test (it needs its own empty site): `bench --site <site> execute
pharmacyos_erp.tests.fresh_site.check`. Raises AssertionError on the first broken guarantee.
"""

import json

import frappe
from frappe.utils import add_days, nowdate

OWNER = "fresh-owner@example.com"
OWNER_PASSWORD = "Fresh-Owner-Pass-1"


def _say(label, value):
	print(f"FRESH {label}: {json.dumps(value, default=str, ensure_ascii=False)}")


def setup():
	from pharmacyos_erp.setup.first_run import setup_pharmacy

	result = setup_pharmacy(
		pharmacy_name="Fresh Site Pharmacy",
		pharmacy_name_ar="صيدلية التثبيت الجديد",
		owner_email=OWNER,
		owner_password=OWNER_PASSWORD,
	)
	_say("first run", result)
	assert result["status"] == "initialized", result


def check():
	from frappe.utils.password import check_password

	from pharmacyos_erp.backup.service import health_check
	from pharmacyos_erp.pharmacy.branches import missing_branch_fields
	from pharmacyos_erp.pharmacy.medicine import create_medicine
	from pharmacyos_erp.pharmacy.receiving import create_receiving_batch
	from pharmacyos_erp.setup.first_run import setup_pharmacy

	missing = missing_branch_fields()
	_say("missing branch columns", missing)
	assert not missing, f"Branch dimension columns missing: {missing}"

	company = frappe.db.get_single_value("Global Defaults", "default_company")
	warehouse = frappe.db.get_value("Branch", {"pharmacyos_warehouse": ["is", "set"]}, "pharmacyos_warehouse")
	code = "FRESH-" + frappe.generate_hash(length=5).upper()
	create_medicine(item_code=code, item_name=f"Fresh {code}", item_group="Products", stock_uom="Nos", standard_rate=2500)
	batch = create_receiving_batch(code, f"B-{code}", add_days(nowdate(), 400))
	supplier = frappe.db.get_value("Supplier", {}, "name") or frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": "Fresh Supplier",
			"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}),
		}
	).insert().name
	receipt = frappe.get_doc(
		{
			"doctype": "Purchase Receipt",
			"company": company,
			"supplier": supplier,
			"items": [
				{
					"item_code": code,
					"qty": 5,
					"rate": 1500,
					"warehouse": warehouse,
					"batch_no": batch["name"],
					"use_serial_batch_fields": 1,
				}
			],
		}
	)
	receipt.insert()
	receipt.submit()
	_say("purchase receipt", receipt.name)

	customer = frappe.db.get_value("Customer", {}, "name") or frappe.get_doc(
		{"doctype": "Customer", "customer_name": "Walk-in", "customer_type": "Individual"}
	).insert().name
	cash = frappe.db.get_value("Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name")
	mode = frappe.get_doc("Mode of Payment", "Cash")
	if not any(a.company == company for a in mode.accounts):
		mode.append("accounts", {"company": company, "default_account": cash})
		mode.save()
	sale = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": company,
			"customer": customer,
			"is_pos": 1,
			"update_stock": 1,
			"items": [{"item_code": code, "qty": 1, "rate": 2500, "warehouse": warehouse}],
			"payments": [{"mode_of_payment": "Cash", "amount": 2500}],
		}
	)
	sale.insert()
	sale.submit()
	_say("counter sale", {"name": sale.name, "status": sale.status, "branch": sale.get("branch")})
	assert sale.get("branch"), "the sale carries no branch"
	frappe.db.commit()

	health = health_check()
	_say("health", health)
	assert health["ok"] and health["critical_branch_fields"], health

	again = setup_pharmacy(pharmacy_name="Someone Else", owner_email=OWNER, owner_password="Changed-Pass-999")
	_say("second first run", again["status"])
	assert again["status"] == "already_initialized", again
	assert frappe.db.get_single_value("PharmacyOS Settings", "pharmacy_name") == "Fresh Site Pharmacy"
	assert check_password(OWNER, OWNER_PASSWORD) == OWNER, "owner password was changed"
	print("FRESH SITE CHECK PASSED")
