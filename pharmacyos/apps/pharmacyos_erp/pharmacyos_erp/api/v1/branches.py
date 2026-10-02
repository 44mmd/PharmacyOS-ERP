"""Branch / warehouse mapping for the PharmacyOS backend."""

import frappe

from pharmacyos_erp.api.v1 import require_integration


@frappe.whitelist(methods=["GET"])
def get_branches() -> dict:
	require_integration()
	rows = frappe.get_all(
		"Branch",
		filters={"pharmacyos_storefront_code": ["is", "set"]},
		fields=[
			"name",
			"pharmacyos_storefront_code as code",
			"pharmacyos_branch_name_ar as name_ar",
			"pharmacyos_warehouse as warehouse",
			"pharmacyos_phone as phone",
			"pharmacyos_address as address",
		],
		order_by="name asc",
	)
	return {"branches": rows}
