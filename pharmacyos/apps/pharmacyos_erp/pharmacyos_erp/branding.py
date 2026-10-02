"""Central PharmacyOS product branding.

Every visible product string, logo path and attribution comes from here (server) or from
`frappe.boot.pharmacyos.brand` (client, filled by `boot.py`). Do not hard-code product names
elsewhere.

Two identities are deliberately kept apart:

* **Platform** (PharmacyOS ERP, by HALF): fixed product identity in the app shell, login, about.
* **Pharmacy** (tenant, from *PharmacyOS Settings*): the business identity shown on invoices and
  receipts, e.g. "Al Noor Pharmacy — Powered by PharmacyOS".

Upstream attribution (ERPNext GPL-3.0, Frappe MIT) is listed in `UPSTREAM` and surfaced in the
About dialog. It must never be removed.
"""

import frappe

ASSETS = "/assets/pharmacyos_erp"

PRODUCT = {
	"product_name": "PharmacyOS ERP",
	"short_name": "PharmacyOS",
	"descriptor": "Pharmacy Operating System",
	"developer": "HALF",
	"attribution": "Designed & Developed by HALF",
	"attribution_short": "by HALF",
	"powered_by": "Powered by PharmacyOS",
	"mark_url": f"{ASSETS}/images/pharmacyos-mark.svg",
	"mark_light_url": f"{ASSETS}/images/pharmacyos-mark-light.svg",
	"favicon_url": f"{ASSETS}/images/pharmacyos-mark.svg",
	"home_route": "/desk/pharmacy-dashboard",
}

UPSTREAM = [
	{
		"name": "ERPNext",
		"copyright": "Frappe Technologies Pvt. Ltd. and contributors",
		"license": "GNU General Public License v3",
		"url": "https://github.com/frappe/erpnext",
	},
	{
		"name": "Frappe Framework",
		"copyright": "Frappe Technologies Pvt. Ltd. and contributors",
		"license": "MIT License",
		"url": "https://github.com/frappe/frappe",
	},
]


def get_pharmacy_identity() -> dict:
	"""The tenant pharmacy's business identity (may be empty on a fresh site)."""
	if not frappe.db.exists("DocType", "PharmacyOS Settings"):
		return {}
	settings = frappe.get_cached_doc("PharmacyOS Settings")
	return {
		"pharmacy_name": settings.pharmacy_name,
		"pharmacy_name_ar": settings.pharmacy_name_ar,
		"pharmacy_logo": settings.pharmacy_logo,
		"license_number": settings.license_number,
		"phone": settings.phone,
		"email": settings.email,
		"address": settings.address,
		"show_powered_by": settings.show_powered_by,
		"receipt_footer": settings.receipt_footer,
	}


def get_brand() -> dict:
	"""Platform brand + pharmacy identity. Safe for Jinja (`get_brand()`) and boot."""
	from pharmacyos_erp import __version__

	return {**PRODUCT, "version": __version__, "upstream": UPSTREAM, "pharmacy": get_pharmacy_identity()}
