"""Fill the return ledger (`pharma_return_ledger`) of every sale that already has submitted returns.

From this version, cumulative returned quantities are kept on the original sale and read under its
row lock (pharmacy/returns.py), so concurrent returns can never exceed the sale. Sales returned before
the upgrade get their ledger computed from their submitted returns here. Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from pharmacyos_erp.pharmacy.returns import RETURN_DOCTYPES, rebuild_ledger
from pharmacyos_erp.setup.custom_fields import CUSTOM_FIELDS


def execute():
	# post_model_sync patches run before after_migrate creates the custom fields: add the column now
	create_custom_fields({dt: CUSTOM_FIELDS[dt] for dt in RETURN_DOCTYPES}, update=True)
	for doctype in RETURN_DOCTYPES:
		originals = frappe.get_all(
			doctype,
			filters={"is_return": 1, "docstatus": 1, "return_against": ["is", "set"]},
			pluck="return_against",
			distinct=True,
		)
		for original in originals:
			rebuild_ledger(doctype, original)
