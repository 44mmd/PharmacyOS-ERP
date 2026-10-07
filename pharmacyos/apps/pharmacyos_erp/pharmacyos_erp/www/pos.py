"""PharmacyOS POS — the counter screen, at `/pos` on the pharmacy's ERP server.

The same page serves both clients: a web browser (Safari, Chrome, Edge on macOS and Windows) and the
PharmacyOS Windows desktop app, which opens this page and adds native printing through its preload
bridge (`window.pharmacyosDesktop`). Sign-in is Frappe's own (`/login`, HTTP-only session cookie);
every action goes through `pharmacyos_erp.pos.api` and ERPNext's POS endpoints with the signed-in
user's own permissions. The page itself carries no data: no prices, stock or costs are rendered here.
"""

import frappe
from frappe import _

from pharmacyos_erp.branding import get_brand

no_cache = 1
sitemap = 0
CSP = (
	"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'; img-src 'self' data: blob:; "
	"font-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; "
	"form-action 'self'; frame-ancestors 'self'"
)


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/pos"
		raise frappe.Redirect
	if frappe.session.data.user_type != "System User":
		frappe.throw(_("The POS is for pharmacy staff."), frappe.PermissionError)

	# Frappe creates the session's CSRF token lazily (the desk does it on boot); the page carries it so
	# every unsafe request of the POS can send it (frappe.csrf_token, injected by Frappe's renderer)
	from frappe.sessions import get_csrf_token

	get_csrf_token()

	# the counter screen loads only its own origin's code: no plug-ins, no framing by other sites (the same
	# policy the POS-host nginx template sets; here it also covers the local server's own nginx)
	frappe.local.response_headers["Content-Security-Policy"] = CSP
	frappe.local.response_headers["X-Frame-Options"] = "SAMEORIGIN"
	frappe.local.response_headers["Referrer-Policy"] = "same-origin"

	brand = get_brand()
	lang = frappe.local.lang or "en"
	context.no_cache = 1
	context.pos_lang = lang
	context.pos_dir = "rtl" if lang in ("ar", "fa", "he", "ur") else "ltr"
	context.brand = brand
	context.title = _("Point of Sale")
	context.build_version = frappe.utils.get_build_version()
	context.messages = messages()
	return context


def messages() -> dict:
	"""Every string the POS screen shows, translated on the server (Arabic first)."""
	return {
		"pos": _("Point of Sale"),
		"search_placeholder": _("Scan a barcode or search medicine name, Arabic name or generic"),
		"no_results": _("No medicine found."),
		"not_found_code": _("No medicine found for code {0}."),
		"choose": _("Several medicines match — choose one."),
		"cart": _("Cart"),
		"cart_empty": _("Scan or search to add medicines."),
		"qty": _("Qty"),
		"price": _("Price"),
		"amount": _("Amount"),
		"discount": _("Discount"),
		"discount_pct": _("Discount %"),
		"remove": _("Remove"),
		"customer": _("Customer"),
		"change_customer": _("Change customer"),
		"walk_in": _("Walk-in customer"),
		"search_customer": _("Search customer name or mobile"),
		"new_customer": _("New customer"),
		"customer_name": _("Customer name"),
		"mobile": _("Mobile"),
		"save": _("Save"),
		"cancel": _("Cancel"),
		"close": _("Close"),
		"subtotal": _("Subtotal"),
		"taxes": _("Taxes"),
		"rounding": _("Rounding"),
		"total": _("Total"),
		"pay": _("Pay"),
		"payment": _("Payment"),
		"tendered": _("Amount received"),
		"exact": _("Exact"),
		"change": _("Change"),
		"remaining": _("Remaining"),
		"complete_sale": _("Complete sale"),
		"completing": _("Completing sale…"),
		"sale_complete": _("Sale complete"),
		"invoice": _("Invoice"),
		"print_receipt": _("Print Receipt"),
		"new_sale": _("New sale"),
		"receipt": _("Receipt"),
		"returns": _("Returns"),
		"return_title": _("Return against a sale"),
		"invoice_no": _("Invoice number"),
		"find": _("Find"),
		"sold": _("Sold"),
		"returnable": _("Can return"),
		"return_qty": _("Return qty"),
		"refund": _("Refund"),
		"refund_preview": _("Refund calculated by the ERP"),
		"confirm_return": _("Confirm return"),
		"return_complete": _("Return complete"),
		"nothing_returnable": _("Everything on this sale has already been returned."),
		"recent": _("Recent sales"),
		"reprint": _("Reprint"),
		"return_short": _("Return"),
		"is_return": _("Return"),
		"shift": _("Shift"),
		"open_shift": _("Open shift"),
		"opening_cash": _("Opening cash"),
		"counter": _("POS Counter"),
		"no_shift": _("No shift is open. Open your shift to start selling."),
		"no_shift_rights": _("No shift is open for you. Ask a pharmacy manager to open your shift."),
		"no_counter": _("No counter (POS Profile) is assigned to you. Ask a pharmacy manager."),
		"no_sell_rights": _("Your account is not allowed to sell at the counter."),
		"close_shift": _("Close shift"),
		"logout": _("Sign out"),
		"offline": _("Connection to the pharmacy server was lost. Nothing is saved until it returns — do not re-enter the sale."),
		"reconnected": _("Connection restored."),
		"retrying": _("Connection lost while completing the sale. Checking with the server — do not re-enter the sale."),
		"session_expired": _("Your session has ended. Sign in again to continue — the cart is kept."),
		"sign_in": _("Sign in"),
		"expired_only": _("Only expired stock — cannot be sold."),
		"out_of_stock": _("Out of stock"),
		"in_stock": _("In stock"),
		"batch": _("Batch"),
		"expiry": _("Exp."),
		"next_batch": _("Next batch (FEFO)"),
		"scanned_batch_expired": _("Batch {0} is expired and cannot be sold."),
		"more_than_stock": _("More than the sellable stock"),
		"prescription": _("Prescription Required"),
		"updating": _("Updating totals…"),
		"error": _("Error"),
		"warning": _("Warning"),
		"browser_print_note": _("Browser printing: choose the receipt printer in the print dialog."),
		"desktop_printed": _("Receipt sent to the printer."),
		"cashier": _("Cashier"),
		"keyboard_help": _("F2 search · F4 customer · F8 hold · F9 pay · Esc close"),
		# Mac keyboards send F-keys only with Fn: ⌘↵ pays there (Ctrl+Enter on Windows works too)
		"keyboard_help_mac": _("⌘ Return pay · Esc close · scan or type at any time"),
		"loading": _("Loading…"),
		"retry": _("Retry"),
		"replayed": _("This sale was already completed — showing the saved invoice."),
		# held (parked) sales: kept on this computer for this user, never reserve stock
		"hold": _("Hold"),
		"held": _("Held sales"),
		"held_empty": _("No held sales."),
		"held_ok": _("Sale held. Resume it from Held sales."),
		"held_note": _("Held sales stay on this computer and do not reserve stock."),
		"nothing_to_hold": _("The cart is empty — nothing to hold."),
		"hold_busy": _("This sale is being completed — it cannot be held now."),
		"resume": _("Resume"),
		"discard": _("Discard"),
		"resumed": _("Sale resumed. Prices and stock are checked again."),
		"items_count": _("{0} items"),
		# closing the shift at the counter
		"close_shift_note": _("Count the drawer and enter what is there for each payment method. The difference is recorded, not corrected."),
		"opening": _("Opening"),
		"expected": _("Expected"),
		"counted": _("Counted"),
		"difference": _("Difference"),
		"shift_sales": _("{0} sales · {1}"),
		"shift_closed": _("Shift closed."),
		"full_closing_form": _("Open the full closing form"),
		"enter_counted": _("Enter the amount counted for {0}."),
		# sale history and voids
		"search_sales": _("Invoice number or customer"),
		"no_sales": _("No sales found."),
		"voided": _("Voided"),
		"void": _("Void"),
		"void_title": _("Void sale {0}"),
		"void_note": _("The whole sale ({0}) is cancelled: stock goes back to its batches and the payment is reversed. The sale stays on record as voided."),
		"void_reason": _("Why is this sale voided?"),
		"void_reason_required": _("Enter the reason for the void."),
		"reason": _("Reason"),
		"manager_approval": _("Manager approval"),
		"manager_email": _("Manager email"),
		"manager_password": _("Manager password"),
		"void_confirm": _("Void sale"),
		"voided_ok": _("Sale {0} voided (approved by {1})."),
		"discount_limit": _("The largest discount you can give is {0}%. A pharmacy manager can give more."),
	}
