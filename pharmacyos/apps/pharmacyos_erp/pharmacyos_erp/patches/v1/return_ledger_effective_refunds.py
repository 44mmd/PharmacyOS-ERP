"""Round 5 (B-2): return ledgers measure refunds in effective money.

The ledger on a root sale (`pharma_return_ledger`) recorded each return's total before ERPNext's
rounding; it now records the amount actually refunded or credited (the rounded total when the return
is rounded, and per-item amounts in that same money). Every root sale with submitted returns gets its
ledger recomputed from its return documents — historical chains included — so existing return
history counts at what it really refunded and creates no additional entitlement: a sale whose earlier
returns were over-refunded by per-document rounding has that much less left to refund. No document,
stock or GL entry is changed.

Idempotent: a second run finds every ledger equal to the recomputed one and writes nothing. (Ledgers
not yet rebuilt are also rebuilt on their first locking read, see `returns._ledger`.)
"""

import json

import frappe

from pharmacyos_erp.pharmacy.returns import LEDGER_FIELD, RETURN_DOCTYPES, compute_ledger, root_sale


def execute():
	for doctype in RETURN_DOCTYPES:
		if not frappe.db.has_column(doctype, LEDGER_FIELD):
			continue
		roots = set()
		for row in frappe.get_all(
			doctype,
			filters={"is_return": 1, "docstatus": 1, "return_against": ["is", "set"]},
			fields=["return_against"],
		):
			roots.add(root_sale(doctype, row.return_against)[0])
		for root in sorted(roots):
			wanted = compute_ledger(doctype, root)
			stored = frappe.db.get_value(doctype, root, LEDGER_FIELD)
			if stored and json.loads(stored) == wanted:
				continue
			frappe.db.set_value(
				doctype,
				root,
				LEDGER_FIELD,
				json.dumps(wanted, sort_keys=True, separators=(",", ":")),
				update_modified=False,
			)
