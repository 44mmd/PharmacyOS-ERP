"""PharmacyOS sign-in page context.

Delegates entirely to Frappe's login context (auth providers, LDAP, signup settings, redirects) and
only adds the PharmacyOS brand. Login itself is still handled by Frappe's /api/method/login.
"""

from frappe.www.login import get_context as frappe_login_context

from pharmacyos_erp.branding import get_brand

no_cache = True


def get_context(context):
	result = frappe_login_context(context)
	context.pharmacyos = get_brand()
	context.title = context.pharmacyos["product_name"]
	return result
