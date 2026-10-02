"""Custom fields PharmacyOS adds to ERPNext DocTypes.

Added with Frappe's `create_custom_fields` (the supported, upgrade-safe mechanism; no core JSON is
edited). All fieldnames are prefixed (`pharma_`, `pharmacyos_`) so they never collide with
upstream fields. Defaults are chosen so existing ERPNext behaviour is unchanged unless an item is
explicitly a medicine.
"""

DISPENSING_OPTIONS = "\nOver the Counter\nPrescription Required"
ROUTE_OPTIONS = "\nOral\nTopical\nInjection\nInhalation\nOphthalmic\nOtic\nNasal\nRectal\nVaginal\nOther"
STORAGE_OPTIONS = "\nRoom Temperature (15-25 °C)\nCool (8-15 °C)\nRefrigerated (2-8 °C)\nFrozen\nOther"

MEDICINE_DEP = "eval:doc.pharma_is_medicine"

CUSTOM_FIELDS = {
	"Item": [
		{
			"fieldname": "pharma_is_medicine",
			"fieldtype": "Check",
			"label": "Medicine",
			"insert_after": "item_group",
			"default": "0",
			"in_standard_filter": 1,
			"description": "Enables pharmacy fields, batch & expiry checks and FEFO guidance for this item.",
		},
		{
			"fieldname": "pharma_section",
			"fieldtype": "Section Break",
			"label": "Medicine",
			"insert_after": "asset_naming_series",
			"depends_on": MEDICINE_DEP,
		},
		{
			"fieldname": "pharma_name_ar",
			"fieldtype": "Data",
			"label": "Arabic Name",
			"insert_after": "pharma_section",
			"in_global_search": 1,
			"translatable": 0,
		},
		{
			"fieldname": "pharma_generic_name",
			"fieldtype": "Data",
			"label": "Generic / Scientific Name",
			"insert_after": "pharma_name_ar",
			"in_global_search": 1,
		},
		{
			"fieldname": "pharma_strength",
			"fieldtype": "Data",
			"label": "Strength",
			"insert_after": "pharma_generic_name",
			"description": "As printed on the pack, e.g. 500 mg, 250 mg/5 ml.",
		},
		{
			"fieldname": "pharma_dosage_form",
			"fieldtype": "Link",
			"label": "Dosage Form",
			"options": "Dosage Form",
			"insert_after": "pharma_strength",
			"in_standard_filter": 1,
		},
		{
			"fieldname": "pharma_cb1",
			"fieldtype": "Column Break",
			"insert_after": "pharma_dosage_form",
		},
		{
			"fieldname": "pharma_pack_size",
			"fieldtype": "Data",
			"label": "Pack Size",
			"insert_after": "pharma_cb1",
			"description": "e.g. 2 x 10 tablets, 100 ml bottle.",
		},
		{
			"fieldname": "pharma_route",
			"fieldtype": "Select",
			"label": "Route",
			"options": ROUTE_OPTIONS,
			"insert_after": "pharma_pack_size",
		},
		{
			"fieldname": "pharma_manufacturer",
			"fieldtype": "Link",
			"label": "Manufacturer",
			"options": "Manufacturer",
			"insert_after": "pharma_route",
		},
		{
			"fieldname": "pharma_dispensing",
			"fieldtype": "Select",
			"label": "Dispensing",
			"options": DISPENSING_OPTIONS,
			"insert_after": "pharma_manufacturer",
			"in_standard_filter": 1,
			"description": "Recorded for information and reporting. PharmacyOS does not enforce prescription rules automatically.",
		},
		{
			"fieldname": "pharma_regulatory_class",
			"fieldtype": "Link",
			"label": "Regulatory Class",
			"options": "Medicine Regulatory Class",
			"insert_after": "pharma_dispensing",
		},
		{
			"fieldname": "pharma_ingredients_section",
			"fieldtype": "Section Break",
			"label": "Active Ingredients",
			"insert_after": "pharma_regulatory_class",
			"depends_on": MEDICINE_DEP,
		},
		{
			"fieldname": "pharma_ingredients",
			"fieldtype": "Table",
			"label": "Active Ingredients",
			"options": "Medicine Ingredient",
			"insert_after": "pharma_ingredients_section",
		},
		{
			"fieldname": "pharma_storage_section",
			"fieldtype": "Section Break",
			"label": "Storage",
			"insert_after": "pharma_ingredients",
			"depends_on": MEDICINE_DEP,
			"collapsible": 1,
		},
		{
			"fieldname": "pharma_storage_condition",
			"fieldtype": "Select",
			"label": "Storage Condition",
			"options": STORAGE_OPTIONS,
			"insert_after": "pharma_storage_section",
		},
		{
			"fieldname": "pharma_cb2",
			"fieldtype": "Column Break",
			"insert_after": "pharma_storage_condition",
		},
		{
			"fieldname": "pharma_storage_notes",
			"fieldtype": "Small Text",
			"label": "Storage Notes",
			"insert_after": "pharma_cb2",
		},
		{
			"fieldname": "pharma_storefront_section",
			"fieldtype": "Section Break",
			"label": "PharmacyOS Storefront",
			"insert_after": "pharma_storage_notes",
			"collapsible": 1,
		},
		{
			"fieldname": "pharmacyos_publish",
			"fieldtype": "Check",
			"label": "Publish to Storefront",
			"insert_after": "pharma_storefront_section",
			"default": "0",
			"description": "Include this item in the catalog and availability returned by the PharmacyOS integration API.",
		},
	],
	"Branch": [
		{
			"fieldname": "pharmacyos_branch_section",
			"fieldtype": "Section Break",
			"label": "Pharmacy Branch",
			"insert_after": "branch",
		},
		{
			"fieldname": "pharmacyos_warehouse",
			"fieldtype": "Link",
			"label": "Branch Warehouse",
			"options": "Warehouse",
			"insert_after": "pharmacyos_branch_section",
			"description": "Sellable stock of this branch. Branch dashboards and the integration API read stock from this warehouse.",
		},
		{
			"fieldname": "pharmacyos_branch_name_ar",
			"fieldtype": "Data",
			"label": "Branch Name (Arabic)",
			"insert_after": "pharmacyos_warehouse",
		},
		{
			"fieldname": "pharmacyos_storefront_code",
			"fieldtype": "Data",
			"label": "Storefront Branch Code",
			"insert_after": "pharmacyos_branch_name_ar",
			"unique": 1,
			"description": "Identifier the PharmacyOS backend uses for this branch.",
		},
		{
			"fieldname": "pharmacyos_branch_cb",
			"fieldtype": "Column Break",
			"insert_after": "pharmacyos_storefront_code",
		},
		{
			"fieldname": "pharmacyos_phone",
			"fieldtype": "Data",
			"label": "Phone",
			"options": "Phone",
			"insert_after": "pharmacyos_branch_cb",
		},
		{
			"fieldname": "pharmacyos_address",
			"fieldtype": "Small Text",
			"label": "Address",
			"insert_after": "pharmacyos_phone",
		},
	],
	"Sales Order": [
		{
			"fieldname": "pharmacyos_order_id",
			"fieldtype": "Data",
			"label": "PharmacyOS Order ID",
			"insert_after": "po_no",
			"unique": 1,
			"read_only": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
			"description": "Set by the PharmacyOS integration API; guarantees an online order is created only once.",
		},
		{
			"fieldname": "pharmacyos_payload_hash",
			"fieldtype": "Data",
			"label": "PharmacyOS Payload Hash",
			"insert_after": "pharmacyos_order_id",
			"read_only": 1,
			"hidden": 1,
			"no_copy": 1,
		},
	],
	"Customer": [
		{
			"fieldname": "pharmacyos_customer_id",
			"fieldtype": "Data",
			"label": "PharmacyOS Customer ID",
			"insert_after": "customer_name",
			"unique": 1,
			"read_only": 1,
			"no_copy": 1,
		},
	],
}
