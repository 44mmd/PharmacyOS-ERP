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


def get_allowed_branches(user: str | None = None) -> list[str] | None:
	"""Branches the user is restricted to by User Permissions, or None when unrestricted.

	Branch is an HR/setup DocType without pharmacy-role permissions in ERPNext. Rather than adding
	Custom DocPerms to it (which would freeze its upstream permissions), PharmacyOS resolves branch
	context from the user's own Branch User Permissions.
	"""
	perms = frappe.permissions.get_user_permissions(user or frappe.session.user).get("Branch")
	return [p.get("doc") for p in perms] if perms else None


def can_access_branch(branch: str) -> bool:
	allowed = get_allowed_branches()
	return bool(frappe.db.exists("Branch", branch)) and (allowed is None or branch in allowed)


def get_user_branches() -> list[dict]:
	"""Branches the current pharmacy user may work in."""
	from pharmacyos_erp.permissions import has_app_permission

	if not has_app_permission():
		return []
	branches = frappe.get_all(
		"Branch",
		fields=["name", "pharmacyos_branch_name_ar as name_ar", "pharmacyos_warehouse as warehouse"],
		order_by="name asc",
		limit_page_length=500,
	)
	allowed = get_allowed_branches()
	return [b for b in branches if allowed is None or b.name in allowed]


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
		if not can_access_branch(branch):
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
		if not can_access_branch(branch):
			frappe.throw(_("You do not have access to branch {0}.").format(branch), frappe.PermissionError)
		permitted = set(get_permitted_warehouses())
		return [w for w in get_branch_warehouses(branch) if w in permitted]
	return None


def get_permitted_warehouses() -> list[str]:
	"""Leaf warehouses the user may read (respects User Permissions)."""
	return frappe.get_list("Warehouse", filters={"is_group": 0}, pluck="name", limit_page_length=0)


# --------------------------------------------------------------------------- branch dimension


def ensure_branch_dimension():
	"""Create ERPNext's Accounting Dimension for Branch (adds `branch` to accounting documents).

	This is ERPNext's supported mechanism for branch-level P&L and GL reporting. Field creation runs
	in ERPNext's background job (queue "long").
	"""
	if frappe.db.exists("Accounting Dimension", {"document_type": "Branch"}):
		return
	frappe.get_doc({"doctype": "Accounting Dimension", "document_type": "Branch", "label": "Branch"}).insert(
		ignore_permissions=True
	)


def get_warehouse_branch_map() -> list[tuple[str, int, int]]:
	"""[(branch, lft, rgt)] for branches with a warehouse (small; read fresh every time)."""
	rows = frappe.get_all(
		"Branch", filters={"pharmacyos_warehouse": ["is", "set"]}, fields=["name", "pharmacyos_warehouse"]
	)
	out = []
	for r in rows:
		lft, rgt = frappe.db.get_value("Warehouse", r.pharmacyos_warehouse, ["lft", "rgt"]) or (None, None)
		if lft is not None:
			out.append((r.name, lft, rgt))
	return out


def branch_for_warehouse(warehouse: str | None) -> str | None:
	if not warehouse:
		return None
	lr = frappe.db.get_value("Warehouse", warehouse, ["lft", "rgt"])
	if not lr:
		return None
	matches = [(b, lft, rgt) for b, lft, rgt in get_warehouse_branch_map() if lft <= lr[0] and lr[1] <= rgt]
	# innermost branch warehouse wins
	return min(matches, key=lambda m: m[2] - m[1])[0] if matches else None


def set_branch_from_warehouse(doc, method=None):
	"""doc_event (validate): fill the Branch dimension from the document's warehouse when empty.

	Never overwrites a branch chosen by the user and does nothing if the dimension is not set up.
	"""
	if not doc.meta.has_field("branch") or doc.get("branch"):
		return
	warehouse = doc.get("set_warehouse") or doc.get("from_warehouse") or doc.get("to_warehouse")
	if not warehouse:
		for row in doc.get("items") or []:
			warehouse = row.get("warehouse") or row.get("s_warehouse") or row.get("t_warehouse")
			if warehouse:
				break
	branch = branch_for_warehouse(warehouse)
	if branch:
		doc.branch = branch


@frappe.whitelist(methods=["POST"])
def assign_user_to_branch(user: str, branch: str) -> None:
	"""Restrict `user` to `branch` and its warehouse with ERPNext User Permissions.

	User Permissions are enforced by Frappe on every list, report, form and API call. Note: a stock
	transfer that touches two branches' warehouses is only visible to users permitted on both.
	"""
	if not (set(frappe.get_roles()) & {"System Manager", "Pharmacy Owner"}):
		frappe.throw(_("Only a Pharmacy Owner can assign branches."), frappe.PermissionError)
	warehouse = frappe.db.get_value("Branch", branch, "pharmacyos_warehouse")
	if not warehouse:
		frappe.throw(_("Set a Branch Warehouse on {0} first.").format(branch))
	for allow, value in (("Branch", branch), ("Warehouse", warehouse)):
		if not frappe.db.exists("User Permission", {"user": user, "allow": allow, "for_value": value}):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": user,
					"allow": allow,
					"for_value": value,
					"apply_to_all_doctypes": 1,
					"is_default": 1,
				}
			).insert(ignore_permissions=True)
	frappe.defaults.set_user_default("branch", branch, user)
