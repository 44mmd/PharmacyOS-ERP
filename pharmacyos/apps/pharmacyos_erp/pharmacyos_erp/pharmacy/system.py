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
		"recent_backups": frappe.get_all(
			"PharmacyOS Backup Log",
			fields=["name", "kind", "status", "started_on", "finished_on", "size_bytes", "verified", "error"],
			order_by="started_on desc",
			limit_page_length=8,
		),
		"can_manage": bool(roles & {"System Manager", "Pharmacy Owner"}),
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
	return items
