"""Adds `frappe.boot.pharmacyos` (brand, expiry windows, branch context) to every Desk session."""

import frappe
from frappe.utils import cint

from pharmacyos_erp.branding import get_brand
from pharmacyos_erp.pharmacy.branches import get_current_branch, get_user_branches
from pharmacyos_erp.setup.install import PHARMACY_ROLES


def boot_session(bootinfo):
	if frappe.session.user == "Guest":
		return

	settings = frappe.get_cached_doc("PharmacyOS Settings")
	roles = set(frappe.get_roles())

	bootinfo.pharmacyos = {
		"brand": get_brand(),
		"expiry": {
			"critical_days": cint(settings.critical_days) or 30,
			"expiring_soon_days": cint(settings.expiring_soon_days) or 90,
		},
		"fefo_mode": settings.fefo_mode or "Warn",
		"branches": get_user_branches(),
		"branch": get_current_branch(),
		"pharmacy_roles": sorted(roles & set(PHARMACY_ROLES)),
	}
