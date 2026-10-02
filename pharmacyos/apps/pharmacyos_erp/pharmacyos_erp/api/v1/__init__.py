"""PharmacyOS integration API, version 1.

Contract: `pharmacyos/docs/INTEGRATION_API.md`. All endpoints are
`/api/method/pharmacyos_erp.api.v1.<module>.<function>`.

* Callers authenticate as a dedicated integration user (API key/secret or OAuth) holding the
  **PharmacyOS Integration** role. Pharmacy staff roles cannot call this API, and Administrator
  credentials must never be used for it.
* Documents are created through normal ERPNext APIs (validation, permissions, audit trail).
* ERPNext is authoritative for prices, stock and order state.
"""

import frappe
from frappe import _

INTEGRATION_ROLE = "PharmacyOS Integration"


def require_integration() -> None:
	roles = set(frappe.get_roles())
	if INTEGRATION_ROLE in roles or "System Manager" in roles:
		return
	frappe.throw(_("This endpoint is reserved for the PharmacyOS integration user."), frappe.PermissionError)


def get_branch_by_code(code: str | None):
	"""Branch for a storefront branch code (Branch.pharmacyos_storefront_code)."""
	if not code:
		return None
	branch = frappe.db.get_value(
		"Branch", {"pharmacyos_storefront_code": code}, ["name", "pharmacyos_warehouse"], as_dict=True
	)
	if not branch or not branch.pharmacyos_warehouse:
		frappe.throw(_("Unknown branch code {0}.").format(code), frappe.DoesNotExistError)
	return branch
