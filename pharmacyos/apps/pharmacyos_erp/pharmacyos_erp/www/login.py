"""PharmacyOS sign-in page context.

Delegates entirely to Frappe's login context (auth providers, LDAP, signup settings, redirects) and
only adds the PharmacyOS brand. Login itself is still handled by Frappe's /api/method/login.
"""

import frappe
from frappe.www.login import get_context as frappe_login_context

from pharmacyos_erp.branding import get_brand

no_cache = True


def get_context(context):
	apply_guest_language()
	result = frappe_login_context(context)
	if context.get("boot"):
		context.boot["lang"] = frappe.local.lang  # <html lang> and client-side translations
	context.pharmacyos = get_brand()
	context.title = context.pharmacyos["product_name"]
	return result


def apply_guest_language():
	"""Sign-in opens in the site's language (Arabic for Iraqi deployments), not the browser's.

	The العربية | English switch (`?_lang=`) is remembered in Frappe's own `preferred_language`
	cookie, so the visitor's explicit choice always wins on later visits.
	"""
	if frappe.session.user != "Guest":
		return
	chosen = frappe.form_dict.get("_lang")
	if chosen and chosen == frappe.local.lang:
		frappe.local.cookie_manager.set_cookie("preferred_language", chosen)
		return
	if chosen or frappe.request.cookies.get("preferred_language"):
		return
	site_language = frappe.db.get_single_value("System Settings", "language")
	if site_language:
		frappe.local.lang = site_language
