"""Pull alternative to webhooks: list recorded integration events (oldest first)."""

import frappe
from frappe.utils import cint

from pharmacyos_erp.api.v1 import require_integration


@frappe.whitelist(methods=["GET"])
def get_events(after: str | None = None, limit: int = 100) -> dict:
	"""Events created after the event `after` (cursor = event name returned last time)."""
	require_integration()
	filters = {}
	if after:
		created = frappe.db.get_value("PharmacyOS Sync Event", after, "creation")
		if created:
			filters["creation"] = [">", created]
	rows = frappe.get_all(
		"PharmacyOS Sync Event",
		filters=filters,
		fields=["name", "event_type", "reference_doctype", "reference_name", "status", "creation"],
		order_by="creation asc",
		limit_page_length=min(max(cint(limit), 1), 500),
	)
	return {"events": rows, "next_cursor": rows[-1].name if rows else after}
