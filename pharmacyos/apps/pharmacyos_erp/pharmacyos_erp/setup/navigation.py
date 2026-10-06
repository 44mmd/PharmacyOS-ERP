"""PharmacyOS navigation — single source of truth for the Desk shell.

Frappe (v17 develop) draws the Desk shell from two app-shipped records:

* a **Dock** (`<app>/dock/<app>/<app>.json`): the icon rail on the left, one row per section;
* **Sidebars** (`<app>/<module>/sidebar/<name>/<name>.json`): the link list for each section.

Both are synced by `bench migrate`. They are generated from `NAVIGATION` below so the information
architecture is reviewable in one place:

	bench --site <site> execute pharmacyos_erp.setup.navigation.write_files   # developer mode only

Only entities that exist are referenced (ERPNext DocTypes, Reports, Pages and PharmacyOS pages).
Frappe's sidebar resolver hides links the current user has no permission for, so navigation never
grants access — server-side permissions stay authoritative.
"""

import json
import os

import frappe

MODULE = "PharmacyOS"
APP = "pharmacyos_erp"
CREATED = "2026-10-02 00:00:00.000000"
# Bump MODIFIED whenever NAVIGATION changes: `bench migrate` only re-imports newer files.
MODIFIED = "2026-10-07 01:00:00.000000"


def link(label, link_type, link_to, icon=None, filters=None):
	return {
		"type": "Link",
		"label": label,
		"link_type": link_type,
		"link_to": link_to,
		"icon": icon,
		"filters": json.dumps(filters) if filters else None,
	}


def section(label):
	return {"type": "Section Break", "label": label}


def dt(label, doctype, icon=None, filters=None):
	return link(label, "DocType", doctype, icon, [[doctype, *f] for f in (filters or [])] or None)


def page(label, name, icon=None):
	return link(label, "Page", name, icon)


def report(label, name, icon=None):
	return link(label, "Report", name, icon)


MEDICINE_FILTER = [["pharma_is_medicine", "=", 1]]

# (sidebar name, dock title, dock icon, items)
# The first sidebar is named after the module so it is the module's default shell.
NAVIGATION = [
	(
		"PharmacyOS",
		"Overview",
		"layout-dashboard",
		[
			page("Dashboard", "pharmacy-dashboard", "layout-dashboard"),
			page("Pharmacy Report", "pharmacy-report", "chart-column"),
			section("Needs attention"),
			page("Batches & Expiry", "batches-expiry", "calendar-clock"),
			page("Inventory Health", "inventory-health", "activity"),
			page("Reorder Suggestions", "reorder-suggestions", "package-check"),
		],
	),
	(
		"Pharmacy",
		"Pharmacy",
		"pill",
		[
			dt("Medicines", "Item", "pill", MEDICINE_FILTER),
			dt("All Products", "Item", "boxes"),
			section("Medicine data"),
			dt("Active Ingredients", "Active Ingredient", "flask-conical"),
			dt("Dosage Forms", "Dosage Form", "tablets"),
			dt("Regulatory Classes", "Medicine Regulatory Class", "shield"),
			dt("Categories", "Item Group", "list-ordered"),
			dt("Brands", "Brand", "tag"),
			dt("Manufacturers", "Manufacturer", "factory"),
			section("Pricing"),
			dt("Item Prices", "Item Price", "badge-percent"),
			dt("Price Lists", "Price List", "notebook-tabs"),
		],
	),
	(
		"Inventory",
		"Inventory",
		"package",
		[
			page("Batches & Expiry", "batches-expiry", "calendar-clock"),
			page("Inventory Health", "inventory-health", "activity"),
			section("Stock"),
			dt("Batches", "Batch", "pill-bottle"),
			dt("Warehouses", "Warehouse", "warehouse"),
			dt("Stock Transfers", "Stock Entry", "arrow-left-right", [["purpose", "=", "Material Transfer"]]),
			dt("Stock Entries", "Stock Entry", "container"),
			dt("Stock Counts", "Stock Reconciliation", "scale"),
			dt("Disposals", "Stock Entry", "trash-2", [["stock_entry_type", "=", "Expired Stock Disposal"]]),
			dt("Material Requests", "Material Request", "clipboard-list"),
			section("Reports"),
			report("Stock Balance", "Stock Balance", "chart-bar"),
			report("Stock Ledger", "Stock Ledger", "history"),
			report("Batch Balance", "Batch-Wise Balance History", "pill-bottle"),
		],
	),
	(
		"Sales",
		"Sales",
		"shopping-cart",
		[
			page("POS", "point-of-sale", "scan-barcode"),
			dt("Sales Invoices", "Sales Invoice", "receipt", [["is_return", "=", 0]]),
			dt("Returns", "Sales Invoice", "undo-2", [["is_return", "=", 1]]),
			dt("Customers", "Customer", "users"),
			dt("Sales Orders", "Sales Order", "file-text"),
			section("POS sessions"),
			dt("POS Profiles", "POS Profile", "store"),
			dt("Opening Entries", "POS Opening Entry", "calendar-clock"),
			dt("Closing Entries", "POS Closing Entry", "list-checks"),
			section("Promotions"),
			dt("Pricing Rules", "Pricing Rule", "badge-percent"),
			dt("Coupon Codes", "Coupon Code", "tag"),
		],
	),
	(
		"Purchasing",
		"Purchasing",
		"truck",
		[
			dt("Suppliers", "Supplier", "building-2"),
			dt("Purchase Orders", "Purchase Order", "file-text"),
			dt("Purchase Receipts", "Purchase Receipt", "package-check"),
			dt("Supplier Invoices", "Purchase Invoice", "receipt"),
			dt("Supplier Returns", "Purchase Receipt", "undo-2", [["is_return", "=", 1]]),
			page("Reorder Suggestions", "reorder-suggestions", "package-check"),
			section("Reports"),
			report("Purchase Analysis", "Purchase Order Analysis", "chart-bar"),
			report("Supplier Obligations", "Accounts Payable", "hand-coins"),
		],
	),
	(
		"Finance",
		"Finance",
		"landmark",
		[
			dt("Expenses", "Journal Entry", "receipt", filters=[["pharma_expense", "=", 1]]),
			dt("Payments", "Payment Entry", "banknote"),
			dt("Journal Entries", "Journal Entry", "notebook-tabs"),
			dt("Payment Methods", "Mode of Payment", "credit-card"),
			dt("Chart of Accounts", "Account", "book-open"),
			section("Statements"),
			report("Receivables", "Accounts Receivable", "hand-coins"),
			report("Payables", "Accounts Payable", "wallet"),
			report("General Ledger", "General Ledger", "book-open"),
			report("Profit and Loss", "Profit and Loss Statement", "chart-pie"),
		],
	),
	(
		"Reports",
		"Reports",
		"chart-bar",
		[
			page("Pharmacy Report", "pharmacy-report", "chart-column"),
			section("Sales"),
			report("Sales Register", "Sales Register", "receipt"),
			report("Item-wise Sales", "Item-wise Sales Register", "pill"),
			report("Gross Profit", "Gross Profit", "trending-up"),
			section("Inventory"),
			report("Stock Balance", "Stock Balance", "chart-bar"),
			report("Stock Ageing", "Stock Ageing", "hourglass"),
			report("Batch Expiry Status", "Batch Item Expiry Status", "calendar-x"),
			report("Projected Stock", "Stock Projected Qty", "trending-up"),
			section("Purchasing"),
			report("Purchase Register", "Purchase Register", "receipt"),
		],
	),
	(
		"Team",
		"Team",
		"users-round",
		[
			dt("Employees", "Employee", "users"),
			dt("Branches", "Branch", "store"),
			dt("Users", "User", "user-cog"),
			dt("Role Profiles", "Role Profile", "shield-check"),
			page("Role Permissions", "permission-manager", "shield"),
		],
	),
	(
		"Intelligence",
		"Intelligence",
		"radar",
		[
			page("Expiry Intelligence", "expiry-intelligence", "calendar-x"),
			page("Inventory Health", "inventory-health", "activity"),
			page("Reorder Suggestions", "reorder-suggestions", "package-check"),
			section("Analytics"),
			report("Sales Analytics", "Sales Analytics", "trending-up"),
			report("Purchase Analytics", "Purchase Analytics", "chart-bar"),
		],
	),
	(
		"Administration",
		"System",
		"settings",
		[
			page("System Status", "system-status", "shield-check"),
			dt("PharmacyOS Settings", "PharmacyOS Settings", "settings"),
			dt("Stock Settings", "Stock Settings", "package"),
			dt("Company", "Company", "building-2"),
			section("Integrations"),
			dt("Sync Events", "PharmacyOS Sync Event", "plug"),
			dt("Backups", "PharmacyOS Backup Log", "database-backup"),
			dt("Voided Sales", "PharmacyOS Void Log", "ban"),
			dt("Webhooks", "Webhook", "plug"),
			section("Audit"),
			dt("Document History", "Version", "history"),
			dt("Activity Log", "Activity Log", "activity"),
		],
	),
]


def sidebar_doc(name, items, claimed):
	"""`claimed` collects (link_type, link_to) already owned by an earlier PharmacyOS sidebar.

	The first PharmacyOS link to an entity is flagged `is_default_module`, which makes the PharmacyOS
	shell the canonical place that entity opens in (Frappe's "owned" rule), so a direct URL to e.g.
	a Purchase Receipt stays inside PharmacyOS navigation.
	"""
	rows = []
	for item in items:
		row = {
			"added": 0,
			"child": 0,
			"collapsible": 0,
			"hidden": 0,
			"indent": 0,
			"is_default_module": 0,
			"keep_closed": 0,
			"open_in_new_tab": 0,
			"show_arrow": 0,
		}
		row.update({k: v for k, v in item.items() if v is not None})
		key = (item.get("link_type"), item.get("link_to"))
		if item.get("type") == "Link" and key not in claimed:
			row["is_default_module"] = 1
			claimed.add(key)
		rows.append(row)
	return {
		"app": APP,
		"creation": CREATED,
		"docstatus": 0,
		"doctype": "Sidebar",
		"header_icon": None,
		"idx": 0,
		"items": rows,
		"modified": MODIFIED,
		"modified_by": "Administrator",
		"module": MODULE,
		"name": name,
		"owner": "Administrator",
		"standard": 1,
		"title": name,
	}


def dock_doc():
	return {
		"app": APP,
		"creation": CREATED,
		"docstatus": 0,
		"doctype": "Dock",
		"idx": 0,
		"items": [
			{"added": 0, "hidden": 0, "icon": icon, "link_to": name, "link_type": "Sidebar", "title": title}
			for name, title, icon, _items in NAVIGATION
		],
		"modified": MODIFIED,
		"modified_by": "Administrator",
		"name": APP,
		"owner": "Administrator",
		"standard": 1,
		"user": "",
	}


def write_files():
	"""Regenerate the shipped Dock and Sidebar JSON files from `NAVIGATION`."""
	app_path = frappe.get_app_path(APP)
	claimed = set()
	for name, _title, icon, items in NAVIGATION:
		doc = sidebar_doc(name, items, claimed)
		doc["header_icon"] = icon
		folder = os.path.join(app_path, "pharmacyos", "sidebar", frappe.scrub(name))
		os.makedirs(folder, exist_ok=True)
		write_json(os.path.join(folder, frappe.scrub(name) + ".json"), doc)

	write_json(os.path.join(app_path, "dock", APP, APP + ".json"), dock_doc())
	write_v16_files(app_path)


# ------------------------------------------------------------------ version-16 shell
# Frappe version-16 draws the desk from app-level `workspace_sidebar/*.json` and
# `desktop_icon/*.json` instead of Dock/Sidebar. Both formats are generated from NAVIGATION and
# shipped together; each Frappe version imports only its own.


def v16_sidebar_name(title):
	return f"PharmacyOS {title}" if title != "PharmacyOS" else "PharmacyOS Overview"


def v16_sidebar_doc(title, icon, items):
	rows, in_section = [], False
	for item in items:
		base = {"child": 0, "collapsible": 1, "indent": 0, "keep_closed": 0, "show_arrow": 0}
		if item["type"] == "Section Break":
			in_section = True
			rows.append(
				{**base, "type": "Section Break", "label": item["label"], "indent": 1, "link_type": "DocType"}
			)
			continue
		row = {
			**base,
			"type": "Link",
			"label": item["label"],
			"link_type": item["link_type"],
			"link_to": item["link_to"],
			"icon": item.get("icon"),
			"child": 1 if in_section else 0,
		}
		if item.get("filters"):
			row["filters"] = item["filters"]
		rows.append(row)
	name = v16_sidebar_name(title)
	return {
		"app": APP,
		"creation": CREATED,
		"docstatus": 0,
		"doctype": "Workspace Sidebar",
		"header_icon": icon,
		"idx": 0,
		"items": rows,
		"modified": MODIFIED,
		"modified_by": "Administrator",
		"module": MODULE,
		"name": name,
		"owner": "Administrator",
		"standard": 1,
		"title": title,
	}


def v16_icon(name, idx, **values):
	return {
		"app": APP,
		"creation": CREATED,
		"docstatus": 0,
		"doctype": "Desktop Icon",
		"hidden": 0,
		"idx": idx,
		"label": name,
		"modified": MODIFIED,
		"modified_by": "Administrator",
		"name": name,
		"owner": "Administrator",
		"roles": [],
		"standard": 1,
		**values,
	}


def write_v16_files(app_path):
	sidebar_dir = os.path.join(app_path, "workspace_sidebar")
	icon_dir = os.path.join(app_path, "desktop_icon")
	os.makedirs(sidebar_dir, exist_ok=True)
	os.makedirs(icon_dir, exist_ok=True)
	write_json(
		os.path.join(icon_dir, "pharmacyos_erp.json"),
		v16_icon(
			"PharmacyOS ERP",
			1,
			icon_type="App",
			link_type="External",
			link="/app/pharmacy-dashboard",
			logo_url="/assets/pharmacyos_erp/images/pharmacyos-mark.svg",
		),
	)
	for idx, (_name, title, icon, items) in enumerate(NAVIGATION, start=1):
		doc = v16_sidebar_doc(title, icon, items)
		write_json(os.path.join(sidebar_dir, frappe.scrub(doc["name"]) + ".json"), doc)
		write_json(
			os.path.join(icon_dir, frappe.scrub(doc["name"]) + ".json"),
			v16_icon(
				doc["name"],
				idx,
				icon=icon,
				icon_type="Link",
				link_to=doc["name"],
				link_type="Workspace Sidebar",
				parent_icon="PharmacyOS ERP",
				restrict_removal=0,
			),
		)


def write_json(path, doc):
	with open(path, "w") as f:
		f.write(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
