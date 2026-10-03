"""Release-candidate remediation for existing sites.

* Branch accounting dimension: create any column ERPNext's background job never created.
* Cashier / Pharmacist / Integration profiles: revoke Accounts User and Stock User and re-sync their
  users now (Frappe would queue that for a worker), and grant the narrow selling permissions.
* Website reservations are protected at the counter by default.
"""

import frappe

from pharmacyos_erp.setup.install import ensure_structure


def execute():
	ensure_structure()
	# a newly added Check field has no stored value on existing sites (it would read as 0 = off)
	stored = frappe.db.sql(
		"select 1 from `tabSingles` where doctype='PharmacyOS Settings' and field='protect_online_reservations'"
	)
	if not stored:
		frappe.db.set_single_value("PharmacyOS Settings", "protect_online_reservations", 1)
