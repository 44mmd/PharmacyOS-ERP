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
#
# Least privilege for counter and integration staff. ERPNext's "Accounts User" grants Journal Entry,
# Payment Entry and GL access, and "Stock User" grants Stock Entry (stock out of thin air), so
# neither is part of the Cashier, Pharmacist or Integration profiles. What those people need to sell
# (Sales/POS Invoice, POS Profile, Mode of Payment, batch bundles, own shift) is granted to their own
# PharmacyOS role by the Custom DocPerms in COUNTER_PERMISSIONS below.
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
	"Pharmacist": ["Pharmacist", "Sales User"],
	"Cashier": ["Cashier", "Sales User"],
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
	# integration API: online orders (Sales Order, Customer) only
	"PharmacyOS Integration": ["PharmacyOS Integration", "Sales User"],
}

# Roles that earlier releases put in these profiles and that must be taken away on upgrade.
REVOKED_PROFILE_ROLES = {
	"Cashier": ("Accounts User", "Stock User"),
	"Pharmacist": ("Accounts User", "Stock User"),
	"PharmacyOS Integration": ("Accounts User", "Stock User"),
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
	frappe.db.delete("Custom DocPerm", {"parent": ["in", SHIFT_DOCTYPES], "role": ["in", SHIFT_ROLES]})
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
	ensure_cashier_shift_access()
	ensure_branch_dimension_fields()


def ensure_branch_dimension_fields():
	"""Repair the Branch accounting dimension's columns if a site was left without them."""
	from pharmacyos_erp.pharmacy.branches import ensure_branch_dimension_fields as repair

	repair()


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
# Permission for Item Price". Read-only access for the roles that sell (the counter permissions below
# are the other Custom DocPerm on core DocTypes). Frappe copies the standard rules first, so existing
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


# Counter selling with the minimum ERPNext permissions, applied as Custom DocPerms on the PharmacyOS
# roles (Frappe copies the standard rules first, so other roles keep their access; removed on
# uninstall):
# * POS shifts: ERPNext lets only Sales Manager open/close them. A cashier runs their own shift:
#   read/create/submit POS Opening and Closing Entries they own (if_owner); no cancel, no delete, no
#   access to other cashiers' shifts.
# * Selling: Sales Invoice / POS Invoice create + submit (no cancel, no delete), POS Profile and Mode
#   of Payment read. ERPNext only grants these through "Accounts User", which would also allow
#   Journal Entries, Payment Entries and GL access.
# * Batch medicines: selling a batch creates a Serial and Batch Bundle, which ERPNext reserves for
#   stock roles; without it a cashier cannot sell any batch-tracked medicine.
# * Pharmacists additionally read batches and the stock ledger (stock and expiry questions) without
#   any stock-creating permission.
_SELL = ("read", "create", "write", "submit", "print", "email")
_COUNTER = {
	"POS Opening Entry": ("read", "create", "write", "submit", "print", "if_owner"),
	"POS Closing Entry": ("read", "create", "write", "submit", "print", "if_owner"),
	"Serial and Batch Bundle": ("read", "create", "write", "submit"),
	"Sales Invoice": _SELL,
	"POS Invoice": _SELL,
	"POS Profile": ("read",),
	"Mode of Payment": ("read",),
}
COUNTER_PERMISSIONS = {
	"Cashier": _COUNTER,
	"Pharmacist": {**_COUNTER, "Batch": ("read",), "Stock Ledger Entry": ("read", "report")},
}
SHIFT_ROLES = tuple(COUNTER_PERMISSIONS)
SHIFT_DOCTYPES = tuple(sorted({dt for perms in COUNTER_PERMISSIONS.values() for dt in perms}))


def ensure_cashier_shift_access():
	from frappe.permissions import add_permission, update_permission_property

	for role, doctypes in COUNTER_PERMISSIONS.items():
		if not frappe.db.exists("Role", role):
			continue
		for doctype, ptypes in doctypes.items():
			if not frappe.db.exists("DocType", doctype):
				continue
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
				add_permission(doctype, role, 0, ptype="read")
			for ptype in ptypes:
				update_permission_property(doctype, role, 0, ptype, 1, validate=False)
			frappe.clear_cache(doctype=doctype)


def ensure_role_profiles():
	"""Create missing profiles, add missing roles and revoke roles listed in REVOKED_PROFILE_ROLES.

	Roles an administrator added to a profile are otherwise left alone. When a profile changes, its
	users are re-synced immediately. Frappe also queues that re-sync for a background worker and
	locks the profile until the job runs; since the work is already done, the lock is released so the
	profile stays editable (and migrate stays re-runnable) on sites without a running worker.
	"""
	for profile, roles in ROLE_PROFILES.items():
		roles = [r for r in roles if frappe.db.exists("Role", r)]
		if not frappe.db.exists("Role Profile", profile):
			frappe.get_doc(
				{"doctype": "Role Profile", "role_profile": profile, "roles": [{"role": r} for r in roles]}
			).insert(ignore_permissions=True)
			continue
		doc = frappe.get_doc("Role Profile", profile)
		revoked = set(REVOKED_PROFILE_ROLES.get(profile, ()))
		existing = {r.role for r in doc.roles}
		missing = [r for r in roles if r not in existing]
		remove = existing & revoked
		if not missing and not remove:
			continue
		doc.set("roles", [r for r in doc.roles if r.role not in revoked])
		for role in missing:
			doc.append("roles", {"role": role})
		doc.save(ignore_permissions=True)
		doc.update_all_users()
		doc.unlock()  # the queued duplicate of update_all_users is harmless when it runs


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

		try:
			_compile("pharmacyos_erp", force=True, verbose=False)
		except TypeError:  # version-16 has no `verbose` argument
			_compile("pharmacyos_erp", force=True)
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
	configure_sessions()
	compile_translations()


def configure_sessions():
	"""Counter terminals are shared: sign out after 12 hours without activity (one long shift).

	Frappe's default keeps an idle desk session for 170 hours. The desktop app keeps the session across
	restarts, so a 12-hour idle limit costs at most one sign-in per shift.
	"""
	frappe.db.set_single_value("System Settings", "session_expiry", "12:00")


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
	frappe.db.set_single_value("PharmacyOS Settings", "protect_online_reservations", 1)


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
