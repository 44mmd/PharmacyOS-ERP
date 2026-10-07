"""A14: the permission matrix, by signing in as each role and trying each operation over HTTP.

Every cell is a real request with that user's session; ✓ = the server did it, ✗ = the server refused it.
Expected values are the documented role model (docs/PRODUCT.md → Roles). Writes permissions-matrix.md.
QA-only; run after qa_seed.py (it creates and cancels its own throw-away documents).
"""

import datetime as dt
import json
import os

from qalib import Refused, Results, Session, rid
from qa_seed import OWNER, STAFF_PWD

R = Results()
ROLES = [
	("Owner", "owner@qa-pharmacy.test", OWNER[1]),
	("Manager", "manager@qa-pharmacy.test", STAFF_PWD),
	("Pharmacist", "pharmacist@qa-pharmacy.test", STAFF_PWD),
	("Cashier", "cashier@qa-pharmacy.test", STAFF_PWD),
	("Purchasing", "purchasing@qa-pharmacy.test", STAFF_PWD),
	("Inventory", "stock@qa-pharmacy.test", STAFF_PWD),
	("Accountant", "accountant@qa-pharmacy.test", STAFF_PWD),
]
today = dt.date.today()
PROFILE = "Main Counter"


def ctx_of(owner):
	wh = owner.get_list("Branch", fields=["pharmacyos_warehouse"], limit=1)[0]["pharmacyos_warehouse"]
	return {"wh": wh, "company": owner.value("Global Defaults", "Global Defaults", "default_company"), "customer": owner.get_doc("POS Profile", PROFILE)["customer"]}


def ops(c):
	"""(label, expected set of roles allowed, fn(session) -> anything)."""
	wh, company = c["wh"], c["company"]
	u = lambda: rid()[3:11]

	def sell(s):
		ctx = s.call("pharmacyos_erp.pos.api.get_context")
		if not ctx.get("shift"):
			s.call("erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher", pos_profile=PROFILE, company=company, balance_details=json.dumps([{"mode_of_payment": "Cash", "opening_amount": 0}]))
		return s.call("pharmacyos_erp.pos.api.checkout", pos_profile=PROFILE, items=[{"item_code": "QA-PAN500", "qty": 1}], payments=[{"mode_of_payment": "Cash", "amount": 2500}], request_id=rid())["name"]

	def cancel_sale(s):
		# a fresh sale by the owner, cancelled directly (the desk Cancel) by this user
		return s.cancel("Sales Invoice", c["owner_sale"]())

	def dispose(s):
		b = s.get_call("pharmacyos_erp.pharmacy.expiry.get_batches", bucket="all", search="PAN-QA-2602")["rows"][0]
		return s.call("pharmacyos_erp.pharmacy.disposal.dispose_batch", item_code="QA-PAN500", batch=b["batch"], warehouse=wh, qty=1, reason="Damaged")

	def see_cost(s):
		rows = s.get_list("Item Price", filters=[["item_code", "=", "QA-PAN500"], ["buying", "=", 1]], fields=["price_list_rate"])
		if not rows:
			raise Refused(403, "no buying price visible")
		return rows

	def report(s):
		return s.call("pharmacyos_erp.pharmacy.reports.get_sales_report", from_date=str(today), to_date=str(today))["costs_visible"]

	def user_admin(s):
		return s.insert({"doctype": "User", "email": f"tmp-{u()}@qa-pharmacy.test", "first_name": "QA temp", "send_welcome_email": 0, "enabled": 0})["name"]

	def settings(s):
		return s.call("frappe.client.set_value", doctype="PharmacyOS Settings", name="PharmacyOS Settings", fieldname="near_expiry_days", value=s.value("PharmacyOS Settings", "PharmacyOS Settings", "near_expiry_days") or 90)

	def backup(s):
		return s.call("pharmacyos_erp.backup.service.backup_now")

	def void_log(s):
		return s.get_list("PharmacyOS Void Log", fields=["name"], limit=1) or "readable"

	def add_medicine(s):
		return s.call("pharmacyos_erp.pharmacy.medicine.create_medicine", item_name=f"QA perm test {u()}", item_group="Analgesics", stock_uom="Nos", standard_rate=1000)

	def supplier(s):
		sg = s.get_list("Supplier Group", filters=[["is_group", "=", 0]], fields=["name"], limit=1)[0]["name"]
		return s.insert({"doctype": "Supplier", "supplier_name": f"QA perm supplier {u()}", "supplier_group": sg})["name"]

	def po(s):
		return s.call("pharmacyos_erp.pharmacy.purchasing.make_purchase_order", supplier="QA Baghdad Pharma Supply", items=[{"item_code": "QA-PAN500", "qty": 1, "warehouse": wh}])

	def receive(s):
		doc = {"doctype": "Purchase Receipt", "supplier": "QA Basra Medical Trading", "company": company, "set_warehouse": wh, "items": []}
		b = s.call("pharmacyos_erp.pharmacy.receiving.create_receiving_batch", item_code="QA-SENSO", batch_id=f"PERM-{u()}", expiry_date=str(today + dt.timedelta(days=700)))
		doc["items"].append({"item_code": "QA-SENSO", "qty": 1, "rate": 2800, "warehouse": wh, "batch_no": b["name"], "use_serial_batch_fields": 1})
		return s.submit(s.insert(doc))["name"]

	def adjust(s):
		b = s.get_call("pharmacyos_erp.pharmacy.expiry.get_batches", bucket="all", search="SEN-QA-2601")["rows"][0]
		return s.submit(s.insert({"doctype": "Stock Reconciliation", "company": company, "purpose": "Stock Reconciliation", "items": [{"item_code": "QA-SENSO", "warehouse": wh, "qty": b["qty"], "batch_no": b["batch"], "use_serial_batch_fields": 1, "valuation_rate": 2800}]}))["name"]

	def batches_page(s):
		return len(s.get_call("pharmacyos_erp.pharmacy.expiry.get_batches", bucket="all", page_length=5)["rows"])

	def journal(s):
		return s.get_list("GL Entry", fields=["name"], limit=1) or "readable"

	ALL = {"Owner", "Manager", "Pharmacist", "Cashier", "Purchasing", "Inventory", "Accountant"}
	return [
		("Read medicines (Item list)", ALL, lambda s: len(s.get_list("Item", fields=["name"], limit=5))),
		("Counter sale (open shift + checkout)", {"Owner", "Manager", "Pharmacist", "Cashier"}, sell),
		("Void / cancel a sale directly", {"Owner", "Manager", "Accountant"}, cancel_sale),
		("Add a medicine with a price", {"Owner", "Manager", "Inventory"}, add_medicine),
		("See purchase cost (buying Item Price)", {"Owner", "Manager", "Purchasing", "Inventory", "Accountant"}, see_cost),
		("Create a supplier", {"Owner", "Manager", "Purchasing"}, supplier),
		("Create a purchase order", {"Owner", "Manager", "Purchasing", "Inventory"}, po),
		("Receive stock (Purchase Receipt, batch + expiry)", {"Owner", "Manager", "Purchasing", "Inventory"}, receive),
		("Stock adjustment (Stock Reconciliation)", {"Owner", "Manager", "Inventory"}, adjust),
		("Dispose of stock (write-off)", {"Owner", "Manager", "Inventory"}, dispose),
		("Batches & Expiry page", {"Owner", "Manager", "Pharmacist", "Cashier", "Purchasing", "Inventory", "Accountant"}, batches_page),
		("Pharmacy Report", {"Owner", "Manager", "Accountant"}, report),
		("Read the void audit log", {"Owner", "Manager", "Accountant"}, void_log),
		("Read the general ledger", {"Owner", "Manager", "Accountant"}, journal),
		("Add a staff account (User)", {"Owner"}, user_admin),
		("Change PharmacyOS Settings", {"Owner"}, settings),
		("Back up now", {"Owner"}, backup),
	]


def main():
	owner = Session("owner").login(*OWNER)
	c = ctx_of(owner)

	def owner_sale():
		ctx = owner.call("pharmacyos_erp.pos.api.get_context")
		if not ctx.get("shift"):
			owner.call("erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher", pos_profile=PROFILE, company=c["company"], balance_details=json.dumps([{"mode_of_payment": "Cash", "opening_amount": 0}]))
		return owner.call("pharmacyos_erp.pos.api.checkout", pos_profile=PROFILE, items=[{"item_code": "QA-PAN500", "qty": 1}], payments=[{"mode_of_payment": "Cash", "amount": 2500}], request_id=rid())["name"]

	c["owner_sale"] = owner_sale
	sessions = {}
	for label, email, pwd in ROLES:
		sessions[label] = Session(label).login(email, pwd)
	matrix = []
	for op, allowed, fn in ops(c):
		row = {"op": op}
		for label, _e, _p in ROLES:
			try:
				fn(sessions[label])
				got = True
				detail = "allowed"
			except Refused as e:
				got = False
				detail = f"refused {e.status}"
			except Exception as e:  # noqa: BLE001 — a crash is a failure, recorded as such
				got = None
				detail = f"error {e}"
			want = label in allowed
			row[label] = ("✓" if got else "✗") + ("" if got == want else " ⚠")
			R.add("A14 Permissions", f"{op} — {label}", "allowed" if want else "refused", detail, got == want)
		matrix.append(row)
	# guest
	g = Session("guest")
	st, _ = g.raw("POST", "/api/method/pharmacyos_erp.pos.api.get_context", {})
	R.add("A14 Permissions", "Guest cannot use the POS API", "403", f"HTTP {st}", st in (401, 403))
	st, html = g.raw("GET", "/pos")
	R.add("A14 Permissions", "Guest opening /pos is sent to sign-in", "redirect or login page", f"HTTP {st}", st in (200, 302, 303) and ("login" in html.lower() or st != 200))

	cols = [r[0] for r in ROLES]
	md = ["| Operation | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
	for row in matrix:
		md.append(f"| {row['op']} | " + " | ".join(row[c_] for c_ in cols) + " |")
	out = os.path.join(os.path.dirname(__file__), "permissions-matrix.md")
	with open(out, "w") as f:
		f.write("\n".join(md) + "\n")
	print("\n".join(md))


if __name__ == "__main__":
	main()
