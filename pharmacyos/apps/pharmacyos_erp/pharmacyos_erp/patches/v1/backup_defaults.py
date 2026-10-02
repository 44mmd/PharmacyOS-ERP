"""Turn on hourly backups and sales spreadsheets on sites installed before they existed."""

import frappe


def execute():
	settings = frappe.get_single("PharmacyOS Settings")
	values = {"enable_hourly_backup": 1, "enable_sales_snapshots": 1}
	for field, default in (("keep_hourly_days", 2), ("keep_daily_days", 30), ("min_free_disk_mb", 1024)):
		if not settings.get(field):
			values[field] = default
	frappe.db.set_single_value("PharmacyOS Settings", values)
