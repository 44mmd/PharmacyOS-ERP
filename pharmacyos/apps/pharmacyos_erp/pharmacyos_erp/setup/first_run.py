"""PharmacyOS first-run setup — replaces ERPNext's generic setup wizard for a new pharmacy.

One call configures a ready-to-sell Iraqi pharmacy:

	bench --site <site> execute pharmacyos_erp.setup.first_run.setup_pharmacy --kwargs "{...}"

* ERPNext's setup engine (company, chart of accounts, fiscal year, fixtures) with Iraqi defaults:
  country Iraq, IQD, Asia/Baghdad, Arabic interface;
* the pharmacy's identity in PharmacyOS Settings (name in Arabic/English, phone, address);
* the first branch with its own stock warehouse (and storefront code for website orders);
* the owner account with the Pharmacy Owner role profile;
* PharmacyOS recommended configuration (FEFO, batch tracking, IQD display, backups on).
Idempotent: safe to run again (existing records are kept).
"""

import frappe
from frappe.utils import getdate, nowdate


def _abbr(name: str) -> str:
	letters = "".join(w[0] for w in name.split() if w and w[0].isascii() and w[0].isalnum()).upper()
	return (letters or "PH")[:5]


@frappe.whitelist(methods=["POST"])
def setup_pharmacy(
	pharmacy_name: str,
	owner_email: str,
	owner_password: str,
	owner_full_name: str = "Pharmacy Owner",
	pharmacy_name_ar: str | None = None,
	phone: str | None = None,
	address: str | None = None,
	branch_name: str = "Main Branch",
	branch_code: str = "MAIN",
	company_abbr: str | None = None,
) -> dict:
	frappe.only_for("System Manager")
	from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

	abbr = company_abbr or _abbr(pharmacy_name)
	year = getdate(nowdate()).year
	if not frappe.is_setup_complete():
		result = setup_complete(
			{
				"language": "English",
				"country": "Iraq",
				"timezone": "Asia/Baghdad",
				"currency": "IQD",
				"full_name": owner_full_name,
				"email": owner_email,
				"password": owner_password,
				"company_name": pharmacy_name,
				"company_abbr": abbr,
				"chart_of_accounts": "Standard",
				"fy_start_date": f"{year}-01-01",
				"fy_end_date": f"{year}-12-31",
				"setup_demo": 0,
			}
		)
		if (result or {}).get("status") not in (None, "ok"):
			frappe.throw(f"Setup failed: {result}")
	frappe.db.commit()

	company = frappe.db.get_value("Company", {"company_name": pharmacy_name}) or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	abbr = frappe.get_cached_value("Company", company, "abbr")

	# first branch and its stock warehouse
	parent = frappe.db.get_value("Warehouse", {"company": company, "is_group": 1}, "name")
	warehouse = frappe.db.get_value("Warehouse", {"warehouse_name": branch_name, "company": company})
	if not warehouse:
		warehouse = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": branch_name,
					"company": company,
					"parent_warehouse": parent,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	if not frappe.db.exists("Branch", branch_name):
		frappe.get_doc(
			{
				"doctype": "Branch",
				"branch": branch_name,
				"pharmacyos_warehouse": warehouse,
				"pharmacyos_storefront_code": branch_code,
			}
		).insert(ignore_permissions=True)

	from pharmacyos_erp.setup.install import apply_recommended_configuration, ensure_structure

	ensure_structure()
	apply_recommended_configuration()  # FEFO, batches, IQD display, Arabic for Iraq, branding

	frappe.db.set_single_value(
		"PharmacyOS Settings",
		{
			"pharmacy_name": pharmacy_name,
			"pharmacy_name_ar": pharmacy_name_ar or pharmacy_name,
			"phone": phone,
			"address": address,
			"online_order_branch": branch_name,
			"enable_hourly_backup": 1,
			"enable_sales_snapshots": 1,
		},
	)
	frappe.db.set_single_value("Stock Settings", "default_warehouse", warehouse)

	from frappe.utils.password import update_password

	if not frappe.db.exists("User", owner_email):
		first, _sep, last = owner_full_name.partition(" ")
		frappe.get_doc(
			{
				"doctype": "User",
				"email": owner_email,
				"first_name": first or owner_full_name,
				"last_name": last or None,
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)
	update_password(owner_email, owner_password)
	if frappe.db.exists("User", owner_email):
		user = frappe.get_doc("User", owner_email)
		if frappe.db.exists("Role Profile", "Pharmacy Owner"):
			user.add_roles(*[r.role for r in frappe.get_doc("Role Profile", "Pharmacy Owner").roles])
		user.language = "ar"
		user.save(ignore_permissions=True)
		frappe.defaults.set_user_default("Company", company, owner_email)
	frappe.db.commit()
	frappe.clear_cache()
	return {"company": company, "abbr": abbr, "branch": branch_name, "warehouse": warehouse}
