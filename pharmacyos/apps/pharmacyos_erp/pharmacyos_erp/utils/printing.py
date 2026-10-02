"""Jinja helpers for PharmacyOS print formats (read-only)."""

import frappe

from pharmacyos_erp.branding import get_brand


def get_item_batches(row) -> list[dict]:
	"""[{batch_id, expiry_date, qty}] for an invoice/receipt item row (batch field or bundle)."""
	names = []
	if row.get("batch_no"):
		names = [(row.batch_no, row.get("stock_qty") or row.get("qty"))]
	elif row.get("serial_and_batch_bundle"):
		names = [
			(e.batch_no, abs(e.qty))
			for e in frappe.get_all(
				"Serial and Batch Entry",
				filters={"parent": row.serial_and_batch_bundle},
				fields=["batch_no", "qty"],
			)
			if e.batch_no
		]
	out = []
	for name, qty in names:
		batch_id, expiry = frappe.db.get_value("Batch", name, ["batch_id", "expiry_date"]) or (name, None)
		out.append({"batch_id": batch_id, "expiry_date": expiry, "qty": qty})
	return out


def get_print_identity(doc) -> dict:
	"""Pharmacy identity for a document, refined by its branch when one is set."""
	brand = get_brand()
	identity = dict(brand.get("pharmacy") or {})
	branch = doc.get("branch") if hasattr(doc, "get") else None
	if branch and frappe.db.exists("Branch", branch):
		b = frappe.db.get_value(
			"Branch",
			branch,
			["name", "pharmacyos_branch_name_ar", "pharmacyos_phone", "pharmacyos_address"],
			as_dict=True,
		)
		identity["branch"] = b.name
		identity["branch_ar"] = b.pharmacyos_branch_name_ar
		identity["phone"] = b.pharmacyos_phone or identity.get("phone")
		identity["address"] = b.pharmacyos_address or identity.get("address")
	identity.setdefault("pharmacy_name", frappe.db.get_value("Company", doc.get("company"), "company_name"))
	identity["platform"] = brand["short_name"]
	identity["powered_by"] = brand["powered_by"]
	return identity
