"""POS test counters and accounts for a developer-mode demo site (never production data).

Run after `demo_data.create_demo_data`:

    bench --site <site> execute pharmacyos_erp.setup.pos_demo.create_pos_demo \\
        --kwargs '{"cashier_password": "…", "manager_password": "…"}'

Creates, idempotently and with real ERPNext documents only:

* the Cash mode of payment on the company's cash account;
* barcodes (EAN-13, "200…" in-store range) on the demo medicines, so a scanner or the keyboard can scan;
* "Counter 1 — Al-Mansour" for the cashier (no price or discount changes, like a real counter) and
  "Counter 2 — Al-Mansour" for the manager (discounts allowed), both selling from the Al-Mansour branch;
* the accounts `cashier@pos-test.invalid` (Cashier role profile) and `manager@pos-test.invalid`
  (Pharmacy Manager role profile), Arabic, with the passwords given. No password is stored in code.
"""

import frappe

from pharmacyos_erp.setup.demo_data import BRANCHES, MEDICINES

CASHIER = "cashier@pos-test.invalid"
MANAGER = "manager@pos-test.invalid"
COUNTER_1 = "Counter 1 - Al-Mansour"
COUNTER_2 = "Counter 2 - Al-Mansour"


def demo_barcode(index: int) -> str:
	"""EAN-13 in the GS1 in-store range (prefix 200), with its check digit."""
	body = f"200000{index:06d}"
	total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
	return body + str((10 - total % 10) % 10)


def create_pos_demo(cashier_password: str, manager_password: str) -> dict:
	if not frappe.conf.developer_mode:
		frappe.throw("POS demo data can only be created on a developer-mode site.")
	frappe.only_for("System Manager")
	if len(cashier_password) < 8 or len(manager_password) < 8:
		frappe.throw("Passwords must have at least 8 characters.")

	from pharmacyos_erp.setup.install import ROLE_PROFILES

	company = frappe.defaults.get_global_default("company")
	abbr = frappe.get_cached_value("Company", company, "abbr")
	warehouse = f"{BRANCHES[0][0]} - {abbr}"
	cash_account = frappe.get_cached_value("Company", company, "default_cash_account")
	currency = frappe.get_cached_value("Company", company, "default_currency")

	mop = frappe.get_doc("Mode of Payment", "Cash")
	if not any(a.company == company for a in mop.accounts):
		mop.append("accounts", {"company": company, "default_account": cash_account})
		mop.save()

	barcodes = {}
	for i, med in enumerate(MEDICINES, start=1):
		code = med[0]
		item = frappe.get_doc("Item", code)
		if not item.barcodes:
			item.append("barcodes", {"barcode": demo_barcode(i), "barcode_type": "EAN"})
			item.save()
		barcodes[code] = item.barcodes[0].barcode

	walk_in = frappe.db.get_value("Customer", {"customer_name": "زبون نقدي"}) or (
		frappe.get_doc(
			{"doctype": "Customer", "customer_name": "زبون نقدي", "customer_type": "Individual"}
		).insert().name
	)

	for email, first_name, profile in (
		(CASHIER, "Test Cashier", "Cashier"),
		(MANAGER, "Test Manager", "Pharmacy Manager"),
	):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"language": "ar",
					"send_welcome_email": 0,
					"role_profile_name": profile if frappe.db.exists("Role Profile", profile) else None,
				}
			).insert(ignore_permissions=True)
		user = frappe.get_doc("User", email)
		if not user.role_profile_name:
			user.add_roles(*ROLE_PROFILES[profile])
		from frappe.utils.password import update_password

		update_password(email, cashier_password if email == CASHIER else manager_password)

	cost_center = frappe.get_cached_value("Company", company, "cost_center")
	write_off = frappe.get_cached_value("Company", company, "write_off_account")
	for name, users, discounts in ((COUNTER_1, [CASHIER], 0), (COUNTER_2, [MANAGER], 1)):
		if frappe.db.exists("POS Profile", name):
			continue
		frappe.get_doc(
			{
				"doctype": "POS Profile",
				"__newname": name,
				"company": company,
				"warehouse": warehouse,
				"currency": currency,
				"customer": walk_in,
				"selling_price_list": frappe.db.get_single_value("Selling Settings", "selling_price_list")
				or "Standard Selling",
				"write_off_account": write_off,
				"write_off_cost_center": cost_center,
				"cost_center": cost_center,
				"update_stock": 1,
				"allow_discount_change": discounts,
				"allow_rate_change": 0,
				"print_format": "PharmacyOS Receipt",
				"payments": [{"mode_of_payment": "Cash", "default": 1}],
				"applicable_for_users": [{"user": u} for u in users],
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()
	return {"counters": [COUNTER_1, COUNTER_2], "users": [CASHIER, MANAGER], "warehouse": warehouse, "barcodes": barcodes}
