"""`is_consolidated` belongs to ERPNext's POS consolidation, not to clients.

POS closing merges the shift's POS Invoices into one consolidated Sales Invoice (and the returns into
a consolidated credit note); those documents carry `is_consolidated = 1`, and ERPNext skips part of its
own calculations for them (item values are taken from the POS Invoices). A hand-built invoice sent
with that flag through the API reached those skipped calculations with empty item values and died
with a `TypeError` in `calculate_net_total` — an uncontrolled HTTP 500 (round-3 finding).

The flag is therefore accepted only while the POS Invoice Merge Log is creating the consolidated
documents (`document_classes.PharmacyPOSInvoiceMergeLog` marks that window); anywhere else it is a
controlled validation error, before ERPNext's calculations run (`before_validate`).

Round 5 (independent re-validation, B-1): the value is classified strictly, never with `cint`. `cint`
turned "true", "yes", "abc", 0.5, [1] or {...} into 0, so they passed the check while ERPNext still
treated them as set and crashed (HTTP 500). Now only the exact representations of "not set" pass —
absent, None, False, 0 / 0.0, "0" and "" (an empty form value) — and are stored as the integer 0;
every other value (any other number, any other text including " ", "true", "false", "no", lists,
objects) is refused with the same controlled ConsolidatedFlagError, before any calculation or write.
"""

from contextlib import contextmanager

import frappe
from frappe import _

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


# the only accepted representations of "not consolidated" (exact matches; bool is checked first)
_NOT_SET_STRINGS = frozenset({"0", ""})


def flag_state(value) -> str:
	"""Classify a raw `is_consolidated` value: "unset", "set" or "invalid". Total and deterministic."""
	if value is None or value is False:
		return "unset"
	if value is True:
		return "set"
	if isinstance(value, int):
		return "unset" if value == 0 else ("set" if value == 1 else "invalid")
	if isinstance(value, float):
		if value != value or value in (float("inf"), float("-inf")):
			return "invalid"
		return "unset" if value == 0.0 else ("set" if value == 1.0 else "invalid")
	if isinstance(value, str):
		if value in _NOT_SET_STRINGS:
			return "unset"
		return "set" if value == "1" else "invalid"
	return "invalid"  # lists, dicts, nested structures, anything else


def _refuse(invalid: bool):
	if invalid:
		message = _(
			"'Is Consolidated' must be 0 or 1; it is set only by POS closing when it merges the shift's invoices."
		)
	else:
		message = _(
			"'Is Consolidated' is set by POS closing when it merges the shift's invoices; it cannot be set on an invoice entered directly."
		)
	frappe.throw(message, ConsolidatedFlagError, title=_("Not a consolidated invoice"))


def guard_consolidated_flag(doc, method=None):
	"""doc_event (before_validate) for Sales Invoice: the strict input boundary of `is_consolidated`."""
	state = flag_state(doc.get("is_consolidated"))
	if state == "unset":
		if doc.get("is_consolidated") is not None:
			doc.is_consolidated = 0  # ERPNext and every later check see a plain integer
		return
	if frappe.flags.get(FLAG):
		if state == "invalid":
			_refuse(invalid=True)
		return  # ERPNext's merge log is creating the consolidated documents
	before = doc.get_doc_before_save() if not doc.is_new() else None
	if state == "set" and before is not None and flag_state(before.get("is_consolidated")) == "set":
		return  # an existing consolidated invoice (e.g. cancelled by its merge log)
	_refuse(invalid=state == "invalid")
