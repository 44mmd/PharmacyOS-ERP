app_name = "pharmacyos_erp"
app_title = "PharmacyOS ERP"
app_publisher = "HALF"
app_description = "PharmacyOS ERP — Pharmacy Operating System, built on ERPNext"
app_email = "noreply@pharmacyos.invalid"
app_license = "gpl-3.0"
app_logo_url = "/assets/pharmacyos_erp/images/pharmacyos-mark.svg"
app_home = "/desk/pharmacy-dashboard"

required_apps = ["frappe/erpnext"]

use_json_request_body = True
export_python_type_annotations = True
require_type_annotated_api_methods = True

add_to_apps_screen = [
	{
		"name": "pharmacyos_erp",
		"logo": "/assets/pharmacyos_erp/images/pharmacyos-mark.svg",
		"title": "PharmacyOS",
		"route": "/desk/pharmacy-dashboard",
		"has_permission": "pharmacyos_erp.permissions.has_app_permission",
	}
]

# Assets ---------------------------------------------------------------------------------------
# Desk: one small global bundle (tokens, shell, IQD formatter, branch chip, about). Heavy screens
# are Frappe Pages whose JS is fetched only when the page is opened.
app_include_css = "pharmacyos.bundle.css"
app_include_js = "pharmacyos.bundle.js"
web_include_css = "pharmacyos_web.bundle.css"

website_context = {
	"favicon": "/assets/pharmacyos_erp/images/pharmacyos-mark.svg",
	"splash_image": "/assets/pharmacyos_erp/images/pharmacyos-mark.svg",
}

jinja = {
	"methods": [
		"pharmacyos_erp.branding.get_brand",
		"pharmacyos_erp.utils.money.format_money",
		"pharmacyos_erp.utils.printing.get_item_batches",
		"pharmacyos_erp.utils.printing.get_print_identity",
		"pharmacyos_erp.utils.dates.format_display_date",
	],
}

# Session / install ------------------------------------------------------------------------------
boot_session = "pharmacyos_erp.boot.boot_session"
after_install = "pharmacyos_erp.setup.install.after_install"
after_migrate = "pharmacyos_erp.setup.install.after_migrate"
before_uninstall = "pharmacyos_erp.setup.install.before_uninstall"

# Extension point: callables `fn(doc, item_row, item)` run for medicine rows of sales documents.
# PharmacyOS ships none — regulated-medicine rules must come from confirmed regulatory requirements.
pharmacyos_sale_validators = []

# Documents --------------------------------------------------------------------------------------
doctype_js = {
	"Item": "public/js/doctype/item.js",
	"Purchase Receipt": "public/js/doctype/purchase_receipt.js",
	"Purchase Invoice": "public/js/doctype/purchase_receipt.js",
}
doctype_list_js = {
	"Item": "public/js/doctype/item_list.js",
	"Journal Entry": "public/js/doctype/journal_entry_list.js",
}

_branch = "pharmacyos_erp.pharmacy.branches.set_branch_from_warehouse"
_sales_validate = [
	_branch,
	"pharmacyos_erp.pharmacy.fefo.check_fefo",
	"pharmacyos_erp.pharmacy.fefo.run_sale_validators",
]
_receipt_validate = [_branch, "pharmacyos_erp.pharmacy.receiving.validate_receipt"]

_outbox = "pharmacyos_erp.integration.outbox"
_fulfilment = f"{_outbox}.on_fulfilment_document"
_order_event = f"{_outbox}.on_sales_order"

doc_events = {
	"Item": {
		"validate": "pharmacyos_erp.pharmacy.medicine.validate_item",
		"on_update": f"{_outbox}.on_item",
	},
	"Sales Invoice": {"validate": _sales_validate, "on_submit": _fulfilment, "on_cancel": _fulfilment},
	"POS Invoice": {"validate": _sales_validate},
	"Delivery Note": {"validate": _sales_validate, "on_submit": _fulfilment, "on_cancel": _fulfilment},
	"Purchase Receipt": {"validate": _receipt_validate},
	"Purchase Invoice": {"validate": _receipt_validate},
	"Sales Order": {
		"validate": _branch,
		"on_submit": _order_event,
		"on_cancel": _order_event,
		"on_update_after_submit": _order_event,
	},
	"Purchase Order": {"validate": _branch},
	"Stock Entry": {"validate": _branch},
	# outbox only records when outbound events are enabled (PharmacyOS Settings)
	"Stock Ledger Entry": {"on_submit": f"{_outbox}.on_stock_ledger_entry"},
}

scheduler_events = {
	"cron": {
		"*/5 * * * *": ["pharmacyos_erp.integration.outbox.process_outbox"],
	},
	# database backup + sales spreadsheet every hour; full backup and day reports once a day
	"hourly_long": ["pharmacyos_erp.backup.service.hourly"],
	"daily_long": ["pharmacyos_erp.backup.service.daily"],
}
