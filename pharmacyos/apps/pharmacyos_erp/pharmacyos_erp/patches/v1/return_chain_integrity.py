"""Round 4: every return counts against its root sale — historical return chains included.

Before this version a return could reference another return (`return_against` pointing at a credit
note), and each link of such a chain opened a fresh allowance equal to the previous return. That
relationship is now refused for new documents (pharmacy/returns.py, ReturnAgainstReturnError).

Historical chains are **kept as they are** — no document is rewritten, cancelled or deleted, and no
stock or GL entry is touched: they are submitted accounting records. What changes is the ledger on
their root sale (`pharma_return_ledger`), which is rebuilt to include every return related to that
sale, directly or through a chain, with the quantities *and* amounts the round-4 rule measures. From
then on no further return can refund or restore what a chain already did, and a root sale that a
chain has over-returned simply has no allowance left. Each such root sale and its chain is listed in
an Error Log entry ("PharmacyOS: historical return chains") for the pharmacy's review.

Idempotent: a second run finds the ledgers already complete and writes (and logs) nothing.
"""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from pharmacyos_erp.pharmacy.returns import (
	LEDGER_FIELD,
	RETURN_DOCTYPES,
	compute_ledger,
	related_returns,
	root_sale,
)
from pharmacyos_erp.setup.custom_fields import CUSTOM_FIELDS


def execute():
	# post_model_sync patches run before after_migrate creates the custom fields: add the column now
	create_custom_fields({dt: CUSTOM_FIELDS[dt] for dt in RETURN_DOCTYPES}, update=True)
	chains, rebuilt = {}, 0
	for doctype in RETURN_DOCTYPES:
		returns = frappe.get_all(
			doctype,
			filters={"is_return": 1, "docstatus": 1, "return_against": ["is", "set"]},
			fields=["name", "return_against"],
		)
		# the root sale of every return (chained returns resolve to the same root as their head)
		roots = {}
		for row in returns:
			root, chain = root_sale(doctype, row.return_against)
			roots[root] = doctype
			if chain:
				chains.setdefault((doctype, root), set()).update([row.name, *chain])
		for root in roots:
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
			rebuilt += 1
	if chains and rebuilt:
		lines = [
			f"{doctype} {root}: returns {', '.join(sorted(names))} (now all counted against {root}; "
			f"related returns: {', '.join(related_returns(doctype, root))})"
			for (doctype, root), names in sorted(chains.items())
		]
		frappe.log_error(
			title="PharmacyOS: historical return chains",
			message=(
				"These submitted returns reference another return instead of the original sale. They "
				"were left untouched; their quantities and amounts now count against the root sale, "
				"which accepts no further return beyond what it sold.\n\n" + "\n".join(lines)
			),
		)
