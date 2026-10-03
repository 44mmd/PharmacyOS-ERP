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


# ------------------------------------------------------------------ integration account isolation

INTEGRATION_ROLE = "PharmacyOS Integration"


def is_integration_account(user: str | None = None) -> bool:
	"""The dedicated Cloud integration user (integration role, no administrator role)."""
	user = user or frappe.session.user
	if not user or user in ("Administrator", "Guest"):
		return False
	roles = set(frappe.get_roles(user))
	return INTEGRATION_ROLE in roles and not roles & set(ADMIN_ROLES)


def user_query_conditions(user: str | None = None) -> str:
	"""permission_query_conditions for User: the integration account sees only itself.

	Every desk user may "select" User records (for link fields), which would let the Cloud's
	credentials enumerate staff accounts and their email addresses.
	"""
	user = user or frappe.session.user
	if is_integration_account(user):
		return f"(`tabUser`.name = {frappe.db.escape(user)})"
	return ""


def user_has_permission(doc, ptype=None, user=None, debug=False) -> bool:
	"""has_permission for User: the integration account may not open other users' records."""
	user = user or frappe.session.user
	if is_integration_account(user):
		return doc.name == user
	return True
