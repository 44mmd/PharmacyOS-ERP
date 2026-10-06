# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt

from frappe.model.document import Document


class PharmacyOSVoidLog(Document):
	"""Who voided which counter sale, who approved it, when and why. Written only by
	`pharmacyos_erp.pos.api.void_sale`; read-only for everyone (the sale itself stays, cancelled)."""

	pass
