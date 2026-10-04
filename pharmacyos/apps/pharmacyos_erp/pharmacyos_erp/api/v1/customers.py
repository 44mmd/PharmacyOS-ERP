"""Customer linkage between PharmacyOS accounts and ERPNext Customers."""

import frappe
from frappe import _

from pharmacyos_erp.api.v1 import require_integration


def ensure_customer(
	customer_id: str, name: str | None = None, phone: str | None = None, email: str | None = None
) -> str:
	"""Return the ERPNext Customer linked to `customer_id`, creating it on first use."""
	if not customer_id:
		frappe.throw(_("customer.id is required."))
	existing = frappe.db.get_value("Customer", {"pharmacyos_customer_id": customer_id})
	if existing:
		return existing
	customer = frappe.new_doc("Customer")
	customer.update(
		{
			"customer_name": (name or f"PharmacyOS {customer_id}").strip(),
			"customer_type": "Individual",
			"pharmacyos_customer_id": customer_id,
			"mobile_no": phone,
			"email_id": email,
		}
	)
	customer.flags.ignore_mandatory = False
	# the integration account has no Customer permission of its own: only this endpoint creates customers
	customer.flags.ignore_permissions = True
	try:
		customer.insert()
	except frappe.DuplicateEntryError:
		# concurrent first order for the same customer: the other request created it
		frappe.db.rollback()
		return frappe.db.get_value("Customer", {"pharmacyos_customer_id": customer_id})
	return customer.name


@frappe.whitelist(methods=["POST"])
def upsert_customer(
	customer_id: str, name: str | None = None, phone: str | None = None, email: str | None = None
) -> dict:
	require_integration()
	customer = ensure_customer(customer_id, name, phone, email)
	updates = {k: v for k, v in {"customer_name": name, "mobile_no": phone, "email_id": email}.items() if v}
	if updates:
		doc = frappe.get_doc("Customer", customer)
		changed = {k: v for k, v in updates.items() if doc.get(k) != v}
		if changed:
			doc.update(changed)
			doc.save(ignore_permissions=True)  # only the fields above, through this endpoint
	return {"customer": customer, "customer_id": customer_id}
