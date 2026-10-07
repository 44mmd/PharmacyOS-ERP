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

# Our own roles. ERPNext document access comes from ERPNext's standard roles bundled in the role
# profiles below, plus the narrow rights in CUSTOM_PERMISSIONS (Custom DocPerms on these roles).
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
# Least privilege for counter and integration staff. ERPNext's standard roles are broad bundles:
# "Accounts User" grants Journal Entries, Payment Entries and GL access; "Stock User" grants Stock
# Entries (stock out of thin air); "Sales User" grants Sales Orders and Stock Reservation Entries
# (which reserve the shelf) and Delivery Notes (which ship stock without a payment). None of them is
# part of the Cashier, Pharmacist or Integration profiles. What those people need — to sell, to look
# medicines up, to run their own shift — is granted to their own PharmacyOS role by CUSTOM_PERMISSIONS
# below; the integration account works only through the PharmacyOS API (`api/v1`).
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
	"Pharmacist": ["Pharmacist"],
	"Cashier": ["Cashier"],
	"Inventory Manager": [
		"Inventory Manager",
		"Stock Manager",
		"Stock User",
		"Item Manager",
		"Purchase User",
	],
	# receiving needs Purchase User only; ERPNext's Stock User would add any stock movement (Material Issue,
	# write-offs), which stays with the stock roles
	"Purchasing Officer": ["Purchasing Officer", "Purchase User", "Purchase Manager"],
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
	# integration API only (`api/v1`): no ERPNext document role at all
	"PharmacyOS Integration": ["PharmacyOS Integration"],
}

# Roles that earlier releases put in these profiles and that must be taken away on upgrade.
REVOKED_PROFILE_ROLES = {
	"Cashier": ("Accounts User", "Stock User", "Sales User"),
	"Pharmacist": ("Accounts User", "Stock User", "Sales User"),
	"PharmacyOS Integration": ("Accounts User", "Stock User", "Sales User"),
	"Purchasing Officer": ("Stock User",),
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
	from pharmacyos_erp.setup.first_run import ensure_counter, is_initialized

	if is_initialized():
		ensure_counter()  # pharmacies set up before setup created the first counter


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
	for role, doctypes in CUSTOM_PERMISSIONS.items():
		frappe.db.delete("Custom DocPerm", {"parent": ["in", list(doctypes)], "role": role})
	from pharmacyos_erp.pharmacy.cost_privacy import remove_cost_privacy

	remove_cost_privacy()
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
	ensure_custom_permissions()
	ensure_cost_privacy()
	ensure_branch_dimension_fields()
	ensure_voucher_indexes()
	ensure_disposal_type()
	ensure_counter_discount_setting()


def ensure_counter_discount_setting():
	"""The counter discount ceiling's default on pharmacies set up before it existed (counter_sales.py)."""
	from pharmacyos_erp.pharmacy.counter_sales import ensure_default_setting

	ensure_default_setting()


def ensure_disposal_type():
	"""The Stock Entry type used to dispose of expired stock (pharmacy/disposal.py)."""
	from pharmacyos_erp.pharmacy.disposal import ensure_disposal_type as apply

	apply()


def ensure_voucher_indexes():
	"""Batch bundle tables indexed by voucher: cancellations lock only their own rows (pharmacy/locking.py)."""
	from pharmacyos_erp.pharmacy.locking import ensure_voucher_indexes as apply

	apply()


def ensure_cost_privacy():
	"""Purchase prices, valuation and stock values: not for counter staff (pharmacy/cost_privacy.py)."""
	from pharmacyos_erp.pharmacy.cost_privacy import ensure_cost_privacy as apply

	apply()


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


# --------------------------------------------------------------------------- permissions
#
# Everything a PharmacyOS role may do beyond the ERPNext roles in its profile, as Custom DocPerms on the
# PharmacyOS role itself (permission level 0). The table is declarative: install and every migrate set
# each (role, DocType) row to exactly these rights, and uninstall removes the rows. Frappe copies a
# DocType's standard rules into Custom DocPerm the first time one is added, so other roles keep their
# access.
#
# Who may do what (the HTTP matrix in tests/test_permissions_matrix.py proves it):
#
# * Item Price (medicine prices; ERPNext v16 reserves it for Sales/Purchase *Master* Manager, which
#   would also hand out customers, price lists, pricing rules and territories):
#   read — every pharmacy role (the POS reads prices with the caller's permissions);
#   create/edit — Pharmacy Owner, Pharmacy Manager, Inventory Manager (ERPNext creates an Item Price
#   when a medicine is added with a selling rate); delete — Pharmacy Owner, Pharmacy Manager.
# * Cancelling a sale (stock and GL reversal) — Pharmacy Owner (Accounts Manager), Pharmacy Manager and
#   Pharmacy Accountant (Accounts Manager). Cancelling a batch sale also cancels its Serial and Batch
#   Bundle, which ERPNext reserves for stock roles: the accountant gets read + cancel on bundles only.
#   Cashiers, pharmacists and branch managers cannot cancel.
# * Counter (Cashier, Pharmacist): sell (Sales/POS Invoice create + submit, no cancel), run their own
#   POS shift (if_owner), create the batch bundles a sale needs, register a walk-in customer, and read
#   the masters the POS screen and the invoice form look up. No Sales Order, Delivery Note, Stock
#   Reservation, Quotation, Journal/Payment Entry, Stock Entry or GL access. Pharmacists additionally
#   read batches and the stock ledger.
# * Branch (HR-only in ERPNext): read for every pharmacy role, maintained by the owner.
# * Batch (ERPNext: Item Manager only): the Purchasing Officer creates the supplier's batch when receiving.
# * Employee records (HR-only in ERPNext; first-run setup removes the HR roles from the owner): the owner
#   keeps the staff directory (no delete: a person who leaves is set to Left, their history stays); the
#   pharmacy manager reads it.

_READ = ("read", "select")
_SELECT = ("select",)
_SELL = ("read", "select", "create", "write", "submit", "print", "email")
_PRICE_ADMIN = ("read", "select", "create", "write", "delete", "report", "export", "print")
_PRICE_EDIT = ("read", "select", "create", "write", "report", "export", "print")

_COUNTER = {
	# selling and the shift
	"Sales Invoice": _SELL,
	"POS Invoice": _SELL,
	"POS Opening Entry": ("read", "create", "write", "submit", "print", "if_owner"),
	"POS Closing Entry": ("read", "create", "write", "submit", "print", "if_owner"),
	"Serial and Batch Bundle": ("read", "create", "write", "submit"),
	"Customer": ("read", "select", "create", "write"),
	# the invoice renders the customer's address and contact (Frappe's "All" role reads only its own)
	"Address": _READ,
	"Contact": _READ,
	# masters the POS screen and the invoice form read (read-only)
	"Item": _READ,
	"Item Group": _READ,
	"Item Price": _READ,
	"Price List": _READ,
	"Brand": _READ,
	"UOM": _READ,
	"Warehouse": _READ,
	"Bin": _READ,
	"Batch": _SELECT,
	"Company": _READ,
	"Currency": _READ,
	"Customer Group": _READ,
	"Territory": _READ,
	"Branch": _READ,
	"POS Profile": _READ,
	"POS Settings": ("read",),
	"Mode of Payment": _READ,
	"Sales Taxes and Charges Template": _READ,
	"Terms and Conditions": _READ,
	"Accounts Settings": ("read",),
	"Selling Settings": ("read",),
	"Stock Settings": ("read",),
	"Fiscal Year": _READ,
	"Account": _SELECT,
	"Cost Center": _SELECT,
	"Item Tax Template": _SELECT,
	"Tax Category": _SELECT,
	"Loyalty Program": _SELECT,
}

_SUPPLIER_MASTER = ("read", "select", "create", "write", "report", "export")
_STAFF_MASTER = ("read", "select", "create", "write", "report", "export", "print")

CUSTOM_PERMISSIONS = {
	"Cashier": _COUNTER,
	"Pharmacist": {
		**_COUNTER,
		"Batch": ("read", "select", "report"),
		"Stock Ledger Entry": ("read", "report"),
	},
	# Suppliers: ERPNext keeps creating them for "Purchase Master Manager", a bundle that also grants full
	# Item Price rights; the pharmacy's buyers get exactly the supplier master instead.
	"Pharmacy Owner": {
		"Item Price": _PRICE_ADMIN,
		"Branch": ("read", "select", "create", "write"),
		"Supplier": _SUPPLIER_MASTER,
		"Employee": _STAFF_MASTER,
		"Designation": ("read", "select", "create", "write"),
		"Department": ("read", "select", "create", "write"),
		"Employment Type": _READ,
	},
	"Pharmacy Manager": {
		"Item Price": _PRICE_ADMIN,
		"Supplier": _SUPPLIER_MASTER,
		"Sales Invoice": ("read", "select", "cancel", "amend"),
		"POS Invoice": ("read", "select", "cancel", "amend"),
		"Branch": _READ,
		"Employee": ("read", "select", "report"),
	},
	"Inventory Manager": {"Item Price": _PRICE_EDIT, "Branch": _READ},
	# Receiving a medicine records the supplier's batch number and expiry (ERPNext reserves Batch for Item
	# Manager): buyers create batches on receipt; correcting a recorded batch stays with stock managers.
	"Purchasing Officer": {"Item Price": _READ, "Branch": _READ, "Supplier": _SUPPLIER_MASTER, "Batch": ("read", "select", "create", "report")},
	"Pharmacy Accountant": {
		"Item Price": _READ,
		# Frappe saves a cancelled document, which checks write as well as cancel (submitted bundles
		# stay immutable; only the accountant's own drafts could be edited, and none are created)
		"Serial and Batch Bundle": ("read", "select", "write", "cancel"),
		"Branch": _READ,
	},
	"Branch Manager": {"Item Price": _READ, "Branch": _READ},
}

_RIGHTS = (
	"select",
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"print",
	"email",
	"report",
	"import",
	"export",
	"share",
)


def ensure_custom_permissions():
	"""Set every (role, DocType) row of CUSTOM_PERMISSIONS to exactly its rights (idempotent)."""
	from frappe.permissions import add_permission

	touched = set()
	for role, doctypes in CUSTOM_PERMISSIONS.items():
		if not frappe.db.exists("Role", role):
			continue
		for doctype, granted in doctypes.items():
			if not frappe.db.exists("DocType", doctype):
				continue
			name = frappe.db.get_value("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0})
			if not name:
				name = add_permission(doctype, role, 0, ptype="read")
			row = frappe.get_doc("Custom DocPerm", name)
			wanted = {right: int(right in granted) for right in _RIGHTS}
			wanted["if_owner"] = int("if_owner" in granted)
			if any(row.get(k) != v for k, v in wanted.items()):
				row.update(wanted)
				row.save(ignore_permissions=True)
			touched.add(doctype)
	for doctype in touched:
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
	# a batch's expiry date decides what is sellable: every change to a batch is kept in its history
	if not frappe.get_meta("Batch").track_changes:
		make_property_setter("Batch", None, "track_changes", 1, "Check", for_doctype=True)


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
