"""PharmacyOS first-run setup — replaces ERPNext's generic setup wizard for a new pharmacy.

One call configures a ready-to-sell Iraqi pharmacy:

	bench --site <site> execute pharmacyos_erp.setup.first_run.setup_pharmacy --kwargs "{...}"

* ERPNext's setup engine (company, chart of accounts, fiscal year, fixtures) with Iraqi defaults:
  country Iraq, IQD, Asia/Baghdad, Arabic interface;
* the pharmacy's identity in PharmacyOS Settings (name in Arabic/English, phone, address);
* the first branch with its own stock warehouse (and storefront code for website orders);
* the Branch accounting dimension with all its columns, created synchronously, so receiving and
  selling work the moment this call returns (no background worker needed);
* the owner account;
* PharmacyOS recommended configuration (FEFO, batch tracking, IQD display, backups on).

Guarantees:

* Inputs are validated before anything is created.
* Safe to re-run after an interruption: whatever is missing is completed, existing records are kept.
* Once the pharmacy is initialised, a further call changes nothing — it does not reset the owner's
  password, replace the pharmacy's identity or touch roles — and returns
  ``{"status": "already_initialized"}``. Identity is edited in PharmacyOS Settings; passwords are
  reset through the standard user screens.
* The owner gets the Pharmacy Owner roles plus System Manager (needed to add staff accounts), not
  every role the generic wizard hands out.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate, validate_email_address


def _abbr(name: str) -> str:
	letters = "".join(w[0] for w in name.split() if w and w[0].isascii() and w[0].isalnum()).upper()
	return (letters or "PH")[:5]


def is_initialized() -> bool:
	"""Setup finished: company created, pharmacy identity recorded and a branch with a warehouse."""
	return bool(
		frappe.is_setup_complete()
		and frappe.db.get_single_value("PharmacyOS Settings", "pharmacy_name")
		and frappe.db.exists("Branch", {"pharmacyos_warehouse": ["is", "set"]})
	)


def owner_roles() -> list[str]:
	from pharmacyos_erp.setup.install import ROLE_PROFILES

	roles = [*ROLE_PROFILES["Pharmacy Owner"], "System Manager"]
	return [r for r in roles if frappe.db.exists("Role", r)]


def _validate_inputs(pharmacy_name, owner_email, owner_password, branch_name, branch_code):
	errors = []
	if not (pharmacy_name or "").strip():
		errors.append(_("Pharmacy name is required."))
	if not validate_email_address((owner_email or "").strip()):
		errors.append(_("Owner email {0} is not a valid email address.").format(owner_email or ""))
	if len(owner_password or "") < 8:
		errors.append(_("Owner password must have at least 8 characters."))
	if not (branch_name or "").strip() or not (branch_code or "").strip():
		errors.append(_("Branch name and branch code are required."))
	if errors:
		frappe.throw("<br>".join(errors), title=_("Cannot set up the pharmacy"))


def _restrict_owner_roles(email: str) -> None:
	user = frappe.get_doc("User", email)
	wanted = set(owner_roles())
	extra = [r.role for r in user.roles if r.role not in wanted]
	if extra:
		user.remove_roles(*extra)
	user = frappe.get_doc("User", email)
	missing = wanted - {r.role for r in user.roles}
	if missing:
		user.add_roles(*sorted(missing))


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
	from pharmacyos_erp.setup.install import apply_recommended_configuration, ensure_structure

	if is_initialized():
		ensure_structure()  # idempotent repair only (e.g. missing dimension columns)
		frappe.db.commit()
		return {
			"status": "already_initialized",
			"company": frappe.db.get_single_value("Global Defaults", "default_company"),
			"pharmacy_name": frappe.db.get_single_value("PharmacyOS Settings", "pharmacy_name"),
			"message": _(
				"This pharmacy is already set up. Nothing was changed: edit the pharmacy's identity in PharmacyOS Settings and reset passwords from the user screens."
			),
		}

	pharmacy_name = (pharmacy_name or "").strip()
	owner_email = (owner_email or "").strip().lower()
	_validate_inputs(pharmacy_name, owner_email, owner_password, branch_name, branch_code)
	from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

	abbr = company_abbr or _abbr(pharmacy_name)
	year = getdate(nowdate()).year
	owner_created_here = False
	if not frappe.is_setup_complete():
		owner_created_here = not frappe.db.exists("User", owner_email)
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

	ensure_structure()
	apply_recommended_configuration()  # FEFO, batches, IQD display, Arabic for Iraq, branding, dimension

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
		owner_created_here = True
	if owner_created_here:
		from frappe.utils.password import update_password

		update_password(owner_email, owner_password)
	_restrict_owner_roles(owner_email)
	user = frappe.get_doc("User", owner_email)
	user.language = "ar"
	user.save(ignore_permissions=True)
	frappe.defaults.set_user_default("Company", company, owner_email)
	frappe.db.commit()
	frappe.clear_cache()
	return {
		"status": "initialized",
		"company": company,
		"abbr": abbr,
		"branch": branch_name,
		"warehouse": warehouse,
	}
