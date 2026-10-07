app_name = "pharmacyos_erp"
app_title = "PharmacyOS ERP"
app_publisher = "HALF"
app_description = "PharmacyOS ERP — Pharmacy Operating System, built on ERPNext"
app_email = "noreply@pharmacyos.invalid"
app_license = "gpl-3.0"
app_logo_url = "/assets/pharmacyos_erp/images/pharmacyos-mark.svg"
from pharmacyos_erp.branding import DESK_BASE

app_home = f"{DESK_BASE}/pharmacy-dashboard"

required_apps = ["frappe/erpnext"]

use_json_request_body = True
export_python_type_annotations = True
require_type_annotated_api_methods = True

add_to_apps_screen = [
	{
		"name": "pharmacyos_erp",
		"logo": "/assets/pharmacyos_erp/images/pharmacyos-mark.svg",
		"title": "PharmacyOS",
		"route": f"{DESK_BASE}/pharmacy-dashboard",
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
_lock_stock = "pharmacyos_erp.pharmacy.stock_guard.lock_document_stock"
_sales_validate = [
	_branch,
	"pharmacyos_erp.pharmacy.fefo.check_fefo",
	"pharmacyos_erp.pharmacy.fefo.run_sale_validators",
	"pharmacyos_erp.pharmacy.returns.validate_return",
	"pharmacyos_erp.pharmacy.stock_guard.protect_reservations",
]
# counter staff sell in their own shift, today, at the pharmacy's prices — whatever made the invoice
# (pharmacy/counter_sales.py)
_invoice_validate = [*_sales_validate, "pharmacyos_erp.pharmacy.counter_sales.validate_counter_sale"]
_receipt_validate = [_branch, "pharmacyos_erp.pharmacy.receiving.validate_receipt"]
# cumulative returned quantities on the original sale (read under its row lock; see pharmacy/returns.py)
_record_return = "pharmacyos_erp.pharmacy.returns.record_return"

_outbox = "pharmacyos_erp.integration.outbox"
_fulfilment = f"{_outbox}.on_fulfilment_document"
_order_event = f"{_outbox}.on_sales_order"

# Lock order for sales documents: Bin rows, then a return's original sale, before Frappe's own row
# locks — on save, submit and cancel (see the module)
override_doctype_class = {
	"Sales Invoice": "pharmacyos_erp.pharmacy.document_classes.PharmacySalesInvoice",
	# marks the only window in which `is_consolidated` may be set (pharmacy/consolidation.py)
	"POS Invoice Merge Log": "pharmacyos_erp.pharmacy.document_classes.PharmacyPOSInvoiceMergeLog",
	"POS Invoice": "pharmacyos_erp.pharmacy.document_classes.PharmacyPOSInvoice",
	"Delivery Note": "pharmacyos_erp.pharmacy.document_classes.PharmacyDeliveryNote",
	"Sales Order": "pharmacyos_erp.pharmacy.document_classes.PharmacySalesOrder",
}

# The integration account cannot list or open staff User records (least privilege for Cloud credentials);
# purchase prices (buying Item Prices) are for cost-reading roles only (pharmacy/cost_privacy.py)
_cost = "pharmacyos_erp.pharmacy.cost_privacy"
permission_query_conditions = {
	"User": "pharmacyos_erp.permissions.user_query_conditions",
	"Item Price": f"{_cost}.item_price_query_conditions",
}
has_permission = {
	"User": "pharmacyos_erp.permissions.user_has_permission",
	"Item Price": f"{_cost}.item_price_has_permission",
}

# ERPNext helpers that compute costs: refused (or answered without the costs) for counter staff
from pharmacyos_erp.pharmacy.cost_privacy import OVERRIDES as _COST_OVERRIDES

override_whitelisted_methods = dict(_COST_OVERRIDES)

# The response boundary: every JSON answer to a user without cost access leaves without cost keys
# (save/submit/insert echoes, helpers returning whole records, report rows, version history)
after_request = [f"{_cost}.scrub_response"]

doc_events = {
	"Item": {
		"validate": "pharmacyos_erp.pharmacy.medicine.validate_item",
		"on_update": f"{_outbox}.on_item",
	},
	"Sales Invoice": {
		# `is_consolidated` only from POS closing (controlled error instead of ERPNext's TypeError)
		"before_validate": ["pharmacyos_erp.pharmacy.consolidation.guard_consolidated_flag", _lock_stock],
		"validate": _invoice_validate,
		"on_submit": [_record_return, _fulfilment],
		"on_cancel": [_record_return, _fulfilment],
	},
	"POS Invoice": {
		"before_validate": _lock_stock,
		"validate": _invoice_validate,
		"on_submit": _record_return,
		"on_cancel": _record_return,
	},
	"Delivery Note": {
		"before_validate": _lock_stock,
		"validate": _sales_validate,
		"on_submit": [_record_return, _fulfilment],
		"on_cancel": [_record_return, _fulfilment],
	},
	# ERP prices are authoritative for the website: a price change alone reaches the Cloud
	"Item Price": {"on_update": f"{_outbox}.on_item_price", "on_trash": f"{_outbox}.on_item_price"},
	"Selling Settings": {"on_update": f"{_outbox}.on_selling_settings"},
	"Price List": {"on_update": f"{_outbox}.on_price_list"},
	"Purchase Receipt": {"validate": _receipt_validate},
	"Purchase Invoice": {"validate": _receipt_validate},
	"Sales Order": {
		"before_validate": _lock_stock,
		"validate": _branch,
		"on_submit": _order_event,
		"on_cancel": _order_event,
		"on_update_after_submit": _order_event,
	},
	"Purchase Order": {"validate": _branch},
	"Stock Entry": {"validate": _branch},
	# an expired batch is disposed of, never re-dated into sellable stock (pharmacy/disposal.py)
	"Batch": {"validate": "pharmacyos_erp.pharmacy.disposal.guard_batch_expiry"},
	# outbox only records when outbound events are enabled (PharmacyOS Settings)
	"Stock Ledger Entry": {"on_submit": f"{_outbox}.on_stock_ledger_entry"},
}

scheduler_events = {
	"cron": {
		# outbound events and website orders (PharmacyOS Cloud), every minute; the pharmacy server
		# always initiates — nothing listens for inbound connections
		"* * * * *": [
			# a price starting or ending at midnight changes the website price with no save
			"pharmacyos_erp.integration.outbox.queue_price_validity_changes",
			"pharmacyos_erp.integration.outbox.process_outbox",
			"pharmacyos_erp.integration.cloud.pull_orders",
		],
	},
	# database backup + sales spreadsheet every hour; full backup and day reports once a day
	"hourly_long": ["pharmacyos_erp.backup.service.hourly"],
	"daily_long": ["pharmacyos_erp.backup.service.daily"],
}
