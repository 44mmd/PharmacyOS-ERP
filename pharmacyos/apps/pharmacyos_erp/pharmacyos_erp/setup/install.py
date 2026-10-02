"""Install / migrate hooks for PharmacyOS ERP.

Two kinds of setup, kept deliberately separate:

* **Structure** (`ensure_structure`): roles, role profiles, custom fields, property setters and
  reference masters. Idempotent; runs on install *and* every migrate so the schema follows the code.
* **Configuration** (`apply_recommended_configuration`): branding, IQD presentation, batch/FEFO stock
  settings and the default app. Runs once on install. It is never re-applied on migrate, so an
  administrator's later changes are respected. Re-run explicitly with
  `bench --site <site> execute pharmacyos_erp.setup.install.apply_recommended_configuration`.

Nothing here touches the stock ledger, GL or existing transactions.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from pharmacyos_erp.branding import PRODUCT
from pharmacyos_erp.setup.custom_fields import CUSTOM_FIELDS

# Our own roles. They grant access to PharmacyOS pages and DocTypes only; ERPNext document access
# comes from ERPNext's standard roles bundled in the role profiles below (no Custom DocPerm on core
# DocTypes, so upstream permission updates keep applying).
ROLES = [
	"Pharmacy Owner",
	"Pharmacy Manager",
	"Pharmacist",
	"Cashier",
	"Inventory Manager",
	"Purchasing Officer",
	"Pharmacy Accountant",
	"Branch Manager",
	"PharmacyOS Integration",
]

PHARMACY_ROLES = [r for r in ROLES if r != "PharmacyOS Integration"]

# Role profile -> roles. Intentionally no "System Manager": owners manage the pharmacy, not the server.
ROLE_PROFILES = {
	"Pharmacy Owner": [
		"Pharmacy Owner",
		"Pharmacy Manager",
		"Inventory Manager",
		"Purchasing Officer",
		"Pharmacy Accountant",
		"Accounts Manager",
		"Accounts User",
		"Stock Manager",
		"Stock User",
		"Item Manager",
		"Sales Manager",
		"Sales User",
		"Purchase Manager",
		"Purchase User",
		"Report Manager",
	],
	"Pharmacy Manager": [
		"Pharmacy Manager",
		"Inventory Manager",
		"Stock Manager",
		"Stock User",
		"Item Manager",
		"Sales Manager",
		"Sales User",
		"Purchase User",
		"Accounts User",
	],
	# ERPNext v17 requires Accounts User to create Sales/POS Invoices.
	"Pharmacist": ["Pharmacist", "Sales User", "Accounts User", "Stock User"],
	"Cashier": ["Cashier", "Sales User", "Accounts User"],
	"Inventory Manager": [
		"Inventory Manager",
		"Stock Manager",
		"Stock User",
		"Item Manager",
		"Purchase User",
	],
	"Purchasing Officer": ["Purchasing Officer", "Purchase User", "Purchase Manager", "Stock User"],
	"Pharmacy Accountant": ["Pharmacy Accountant", "Accounts User", "Accounts Manager"],
	# runs one branch: restrict with a Branch User Permission; opens/closes POS shifts (Sales Manager)
	"Branch Manager": [
		"Branch Manager",
		"Sales Manager",
		"Sales User",
		"Accounts User",
		"Stock User",
		"Purchase User",
	],
	"PharmacyOS Integration": ["PharmacyOS Integration", "Sales User", "Stock User"],
}

DOSAGE_FORMS = [
	("Tablet", "أقراص", "TAB"),
	("Capsule", "كبسول", "CAP"),
	("Syrup", "شراب", "SYR"),
	("Suspension", "معلق", "SUSP"),
	("Injection", "حقن", "INJ"),
	("Cream", "كريم", "CRM"),
	("Ointment", "مرهم", "OINT"),
	("Gel", "جل", "GEL"),
	("Drops", "قطرات", "DRP"),
	("Inhaler", "بخاخ استنشاق", "INH"),
	("Nasal Spray", "بخاخ أنفي", "NS"),
	("Suppository", "تحاميل", "SUPP"),
	("Sachet", "أكياس", "SACH"),
	("Solution", "محلول", "SOL"),
	("Patch", "لصقة", "PATCH"),
]


def after_install():
	ensure_structure()
	apply_recommended_configuration()


def after_migrate():
	ensure_structure()
	compile_translations()


def before_uninstall():
	"""Remove only what PharmacyOS added to ERPNext DocTypes.

	Stock, accounting and item data stay in ERPNext's tables; only the PharmacyOS columns
	(custom fields) and the Item search property setter are dropped, so ERPNext forms keep working.
	"""
	for doctype, fields in CUSTOM_FIELDS.items():
		for field in fields:
			name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": field["fieldname"]})
			if name:
				frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
	frappe.db.delete("Property Setter", {"doc_type": "Item", "property": "search_fields"})
	frappe.db.delete("Custom DocPerm", {"parent": "Item Price", "role": ["in", PRICE_READ_ROLES]})
	for doctype, fieldname in DESCRIPTION_OVERRIDES:
		frappe.db.delete(
			"Property Setter", {"doc_type": doctype, "field_name": fieldname, "property": "description"}
		)
	frappe.clear_cache()


# --------------------------------------------------------------------------- structure


def ensure_structure():
	ensure_roles()
	create_custom_fields(CUSTOM_FIELDS, update=True)
	ensure_property_setters()
	ensure_description_overrides()
	ensure_role_profiles()
	ensure_dosage_forms()
	ensure_price_read_access()


def ensure_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc(
				{
					"doctype": "Role",
					"role_name": role,
					"desk_access": 1,
					"is_custom": 0,
				}
			).insert(ignore_permissions=True)


# ERPNext v17 lets only Sales/Purchase Master Manager read Item Price, but the POS barcode/serial
# search reads it with the caller's permissions, so a cashier's scan fails with "Insufficient
# Permission for Item Price". The only exception to the "no Custom DocPerm on core DocTypes" rule:
# read-only access for the roles that sell. Frappe copies the standard rules first, so existing
# access is unchanged; the rows are removed on uninstall.
PRICE_READ_ROLES = ("Pharmacy Owner", "Pharmacy Manager", "Branch Manager", "Pharmacist", "Cashier")


def ensure_price_read_access():
	from frappe.permissions import add_permission, update_permission_property

	for role in PRICE_READ_ROLES:
		if not frappe.db.exists("Role", role):
			continue
		if not frappe.db.exists("Custom DocPerm", {"parent": "Item Price", "role": role, "permlevel": 0}):
			add_permission("Item Price", role, 0, ptype="read")
		update_permission_property("Item Price", role, 0, "select", 1, validate=False)


def ensure_role_profiles():
	for profile, roles in ROLE_PROFILES.items():
		roles = [r for r in roles if frappe.db.exists("Role", r)]
		if frappe.db.exists("Role Profile", profile):
			doc = frappe.get_doc("Role Profile", profile)
			existing = {r.role for r in doc.roles}
			missing = [r for r in roles if r not in existing]
			if not missing:
				continue
			for role in missing:
				doc.append("roles", {"role": role})
			doc.save(ignore_permissions=True)
		else:
			frappe.get_doc(
				{"doctype": "Role Profile", "role_profile": profile, "roles": [{"role": r} for r in roles]}
			).insert(ignore_permissions=True)


def ensure_property_setters():
	# Make Arabic and generic names searchable wherever an Item link is searched.
	search_fields = frappe.get_meta("Item").search_fields or ""
	fields = [f.strip() for f in search_fields.split(",") if f.strip()]
	for extra in ("pharma_name_ar", "pharma_generic_name", "pharma_search_key"):
		if extra not in fields:
			fields.append(extra)
	new_value = ",".join(fields)
	if new_value != search_fields:
		make_property_setter("Item", None, "search_fields", new_value, "Data", for_doctype=True)


# Field help texts that name ERPNext on forms pharmacy staff use daily (presentation only).
DESCRIPTION_OVERRIDES = {
	("Item", "is_stock_item"): (
		"PharmacyOS records every stock movement of this item in the stock ledger. "
		"Keep unchecked for services and other non-stock items."
	),
}


def ensure_description_overrides():
	for (doctype, fieldname), text in DESCRIPTION_OVERRIDES.items():
		make_property_setter(doctype, fieldname, "description", text, "Small Text")


def ensure_dosage_forms():
	for name, name_ar, abbr in DOSAGE_FORMS:
		if not frappe.db.exists("Dosage Form", name):
			frappe.get_doc(
				{
					"doctype": "Dosage Form",
					"dosage_form": name,
					"dosage_form_ar": name_ar,
					"abbreviation": abbr,
				}
			).insert(ignore_permissions=True)


def compile_translations():
	try:
		from frappe.gettext.translate import compile_translations as _compile

		_compile("pharmacyos_erp", force=True, verbose=False)
	except Exception:
		frappe.log_error(_("PharmacyOS: could not compile translations"))


# --------------------------------------------------------------------------- configuration


def apply_recommended_configuration():
	configure_branding()
	configure_locale()
	configure_currency()
	configure_stock()
	configure_pos_search()
	configure_branch_dimension()
	configure_print_formats()
	compile_translations()


def configure_print_formats():
	"""PharmacyOS invoice as Sales Invoice default (Property Setter; users can still pick others)."""
	if frappe.db.exists("Print Format", "PharmacyOS Invoice"):
		make_property_setter(
			"Sales Invoice", None, "default_print_format", "PharmacyOS Invoice", "Data", for_doctype=True
		)


def configure_branch_dimension():
	from pharmacyos_erp.pharmacy.branches import ensure_branch_dimension

	ensure_branch_dimension()


def configure_branding():
	"""Replace stock product branding in end-user surfaces. Licence/attribution files are untouched."""
	website = frappe.get_single("Website Settings")
	website.app_name = PRODUCT["product_name"]
	website.app_logo = PRODUCT["mark_url"]
	website.favicon = PRODUCT["favicon_url"]
	website.splash_image = PRODUCT["mark_url"]
	website.flags.ignore_mandatory = True
	website.save(ignore_permissions=True)

	navbar = frappe.get_single("Navbar Settings")
	navbar.app_logo = PRODUCT["mark_url"]
	navbar.save(ignore_permissions=True)

	frappe.db.set_single_value(
		"System Settings",
		{
			"app_name": PRODUCT["product_name"],
			"default_app": "pharmacyos_erp",
			# drops the hook-provided "Sent via ERPNext" footer from outgoing email
			"disable_standard_email_footer": 1,
			# ERPNext's module "Getting Started" panels are replaced by PharmacyOS workflows
			"enable_onboarding": 0,
		},
	)


def configure_locale():
	"""Iraqi deployments default to Arabic (RTL) and Baghdad time.

	Applied only when the site's country is Iraq, so other deployments (and upstream test sites) keep
	their language. Users still switch per account (My Settings → Language), and English stays fully
	available. Only defaults are set here; existing users' explicit language choices are untouched.
	"""
	if frappe.db.get_single_value("System Settings", "country") != "Iraq":
		return
	frappe.db.set_single_value("System Settings", "time_zone", "Asia/Baghdad")
	if frappe.db.exists("Language", "ar"):
		frappe.db.set_single_value("System Settings", "language", "ar")
		frappe.db.set_default("lang", "ar")


def configure_currency():
	"""Enable IQD with locale-aware symbol and right-side placement.

	Storage precision is NOT reduced: unit and valuation rates keep their decimals. Whole-dinar
	presentation is done by the PharmacyOS formatter (see `public/js/pharmacyos/money.js`), which only
	hides an all-zero fraction. The symbol is stored as "IQD" and translated to "د.ع" for Arabic users.
	"""
	if not frappe.db.exists("Currency", "IQD"):
		return
	frappe.db.set_value(
		"Currency",
		"IQD",
		{"enabled": 1, "symbol": "IQD", "symbol_on_right": 1, "smallest_currency_fraction_value": 0},
	)


def configure_stock():
	"""Batch tracking on, and First-Expiry-First-Out for automatic batch selection (an ERPNext setting)."""
	frappe.db.set_single_value(
		"Stock Settings",
		{"enable_serial_and_batch_no_for_item": 1, "pick_serial_and_batch_based_on": "Expiry"},
	)


def configure_pos_search():
	"""Let the ERPNext POS find medicines by Arabic and generic name (POS Settings search fields)."""
	if not frappe.db.exists("DocType", "POS Settings"):
		return
	from erpnext.accounts.doctype.pos_settings.pos_settings import (
		get_search_field_option,
		get_searchable_item_fields,
	)

	searchable = {df.fieldname: df for df in get_searchable_item_fields()}
	pos_settings = frappe.get_single("POS Settings")
	existing = {row.fieldname for row in pos_settings.get("pos_search_fields", [])}
	changed = False
	for fieldname in ("pharma_name_ar", "pharma_generic_name", "pharma_search_key"):
		df = searchable.get(fieldname)
		if df and fieldname not in existing:
			pos_settings.append(
				"pos_search_fields", {"fieldname": fieldname, "field": get_search_field_option(df)}
			)
			changed = True
	if changed:
		pos_settings.save(ignore_permissions=True)
