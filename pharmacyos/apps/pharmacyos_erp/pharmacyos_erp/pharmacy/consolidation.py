"""`is_consolidated` belongs to ERPNext's POS consolidation, not to clients.

POS closing merges the shift's POS Invoices into one consolidated Sales Invoice (and the returns into
a consolidated credit note); those documents carry `is_consolidated = 1`, and ERPNext skips part of its
own calculations for them (item values are taken from the POS Invoices). A hand-built invoice sent
with that flag through the API reached those skipped calculations with empty item values and died
with a `TypeError` in `calculate_net_total` — an uncontrolled HTTP 500 (round-3 finding).

The flag is therefore accepted only while the POS Invoice Merge Log is creating the consolidated
documents (`document_classes.PharmacyPOSInvoiceMergeLog` marks that window); anywhere else it is a
controlled validation error, before ERPNext's calculations run (`before_validate`).
"""

from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import cint

FLAG = "pharmacyos_pos_consolidation"


class ConsolidatedFlagError(frappe.ValidationError):
	"""`is_consolidated` set on a document that POS consolidation is not creating."""


@contextmanager
def consolidating():
	"""Mark the transaction window in which ERPNext's merge log writes consolidated documents."""
	previous = frappe.flags.get(FLAG)
	frappe.flags[FLAG] = True
	try:
		yield
	finally:
		frappe.flags[FLAG] = previous


def guard_consolidated_flag(doc, method=None):
	"""doc_event (before_validate) for Sales Invoice."""
	if not cint(doc.get("is_consolidated")) or frappe.flags.get(FLAG):
		return
	before = doc.get_doc_before_save() if not doc.is_new() else None
	if before is not None and cint(before.get("is_consolidated")):
		return  # an existing consolidated invoice (e.g. cancelled by its merge log)
	frappe.throw(
		_(
			"'Is Consolidated' is set by POS closing when it merges the shift's invoices; it cannot be set on an invoice entered directly."
		),
		ConsolidatedFlagError,
		title=_("Not a consolidated invoice"),
	)
