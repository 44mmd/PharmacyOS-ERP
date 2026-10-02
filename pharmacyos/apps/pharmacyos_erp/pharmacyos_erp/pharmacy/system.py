"""System Status: backup protection and cloud synchronization, in pharmacy terms."""

import frappe

from pharmacyos_erp.backup.service import STATUS_ROLES, backup_health
from pharmacyos_erp.integration.outbox import sync_status


@frappe.whitelist()
def get_system_status() -> dict:
	frappe.only_for(STATUS_ROLES)
	roles = set(frappe.get_roles())
	return {
		"backup": backup_health(),
		"sync": sync_status(),
		"cloud": cloud_status(),
		"recent_backups": frappe.get_all(
			"PharmacyOS Backup Log",
			fields=["name", "kind", "status", "started_on", "finished_on", "size_bytes", "verified", "error"],
			order_by="started_on desc",
			limit_page_length=8,
		),
		"can_manage": bool(roles & {"System Manager", "Pharmacy Owner"}),
	}


def cloud_status() -> dict:
	"""Website orders connection (pull side)."""
	from pharmacyos_erp.integration.cloud import cloud_enabled

	settings = frappe.get_cached_doc("PharmacyOS Settings")
	return {
		"enabled": cloud_enabled(),
		"last_pull": settings.get("cloud_last_pull"),
		"last_error": settings.get("cloud_last_error"),
		"open_website_orders": frappe.db.count(
			"Sales Order",
			{
				"pharmacyos_order_id": ["is", "set"],
				"docstatus": 1,
				"status": ["not in", ["Completed", "Closed"]],
			},
		),
	}


def attention_items() -> list[dict]:
	"""Dashboard "Needs attention" entries; empty when everything is healthy (quiet success)."""
	if not set(frappe.get_roles()) & set(STATUS_ROLES):
		return []
	items = []
	backup = backup_health()
	if backup["enabled"] and backup["state"] != "protected":
		items.append({"kind": "backup", "count": 1})
	sync = sync_status()
	if sync["state"] in ("offline", "attention"):
		items.append(
			{"kind": "sync", "count": sync["failed"] or sync["pending"] or 1, "state": sync["state"]}
		)
	cloud = cloud_status()
	if cloud["enabled"] and cloud["last_error"]:
		items.append({"kind": "sync", "count": 1, "state": "offline"})
	if cloud["open_website_orders"]:
		items.append({"kind": "web_orders", "count": cloud["open_website_orders"]})
	return items
