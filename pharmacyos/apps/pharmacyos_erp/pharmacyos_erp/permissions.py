"""Permission helpers. Server-side checks are authoritative; the UI only mirrors them."""

import frappe
from frappe import _

from pharmacyos_erp.setup.install import PHARMACY_ROLES

ADMIN_ROLES = ("System Manager", "Administrator")


def has_app_permission() -> bool:
	"""Whether the PharmacyOS app tile is shown on the apps screen."""
	if frappe.session.user == "Administrator":
		return True
	roles = set(frappe.get_roles())
	return bool(roles & (set(PHARMACY_ROLES) | set(ADMIN_ROLES)))


def require_pharmacy_role(*roles: str) -> None:
	"""Throw PermissionError unless the user has one of `roles` (or any pharmacy role if none given)."""
	allowed = set(roles or PHARMACY_ROLES) | set(ADMIN_ROLES)
	if frappe.session.user == "Administrator" or allowed & set(frappe.get_roles()):
		return
	frappe.throw(_("You do not have access to this PharmacyOS view."), frappe.PermissionError)
