"""Branch context.

PharmacyOS models a physical pharmacy branch as ERPNext's **Branch** with a linked **warehouse**
(custom field `pharmacyos_warehouse`; it may be a group warehouse). One Company is shared by all
branches. Isolation between branches uses ERPNext **User Permissions** on Branch and Warehouse, so
it is enforced by the framework on every list, report and API call, not by the UI.

The *current* branch is a per-user default (`frappe.defaults` key `branch`). Choosing a branch only
changes the context of PharmacyOS views; it never grants access.
"""

import frappe
from frappe import _


def get_user_branches() -> list[dict]:
	"""Branches the current user may read (User Permissions apply through `get_list`)."""
	if not frappe.has_permission("Branch", "read"):
		return []
	return frappe.get_list(
		"Branch",
		fields=["name", "pharmacyos_branch_name_ar as name_ar", "pharmacyos_warehouse as warehouse"],
		order_by="name asc",
		limit_page_length=500,
	)


def get_current_branch() -> str | None:
	branches = [b.name for b in get_user_branches()]
	if not branches:
		return None
	current = frappe.defaults.get_user_default("branch")
	if current in branches:
		return current
	return branches[0] if len(branches) == 1 else None


@frappe.whitelist()
def set_current_branch(branch: str | None = None) -> str | None:
	"""Switch the PharmacyOS branch context. Empty clears it (all permitted branches)."""
	if branch:
		if not frappe.db.exists("Branch", branch) or not frappe.has_permission("Branch", "read", branch):
			frappe.throw(_("You do not have access to branch {0}.").format(branch), frappe.PermissionError)
		frappe.defaults.set_user_default("branch", branch)
	else:
		frappe.defaults.clear_user_default("branch")
	return branch or None


def get_branch_warehouses(branch: str | None) -> list[str] | None:
	"""Leaf warehouses belonging to `branch` (descendants when its warehouse is a group).

	Returns None when no branch is given (= no branch filter). Returns [] when the branch has no
	warehouse configured, so callers show an explicit empty state instead of every warehouse.
	"""
	if not branch:
		return None
	warehouse = frappe.db.get_value("Branch", branch, "pharmacyos_warehouse")
	if not warehouse:
		return []
	lft, rgt = frappe.db.get_value("Warehouse", warehouse, ["lft", "rgt"])
	return frappe.get_all(
		"Warehouse",
		filters={"lft": [">=", lft], "rgt": ["<=", rgt], "is_group": 0},
		pluck="name",
	)


def resolve_warehouses(branch: str | None = None, warehouse: str | None = None) -> list[str] | None:
	"""Warehouses a PharmacyOS view should aggregate, honouring branch context and permissions.

	* explicit `warehouse` wins (must be readable);
	* else the branch's warehouses;
	* else None (all warehouses the user may read — callers must still apply permission filters).
	"""
	if warehouse:
		if not frappe.has_permission("Warehouse", "read", warehouse):
			frappe.throw(
				_("You do not have access to warehouse {0}.").format(warehouse), frappe.PermissionError
			)
		return [warehouse]
	if branch:
		if not frappe.has_permission("Branch", "read", branch):
			frappe.throw(_("You do not have access to branch {0}.").format(branch), frappe.PermissionError)
		return get_branch_warehouses(branch)
	return None


def get_permitted_warehouses() -> list[str]:
	"""Leaf warehouses the user may read (respects User Permissions)."""
	return frappe.get_list("Warehouse", filters={"is_group": 0}, pluck="name", limit_page_length=0)
