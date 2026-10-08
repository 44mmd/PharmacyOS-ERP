"""A5 + A6: the QA TEST pharmacy's staff, suppliers and medicines, then purchasing as the Purchasing Officer:
Supplier → Purchase Order → partial receipt → full receipt (batches, expiry, cost) → stock → supplier return.

QA-only data, created through the same HTTP API the screens use, as the users who would do it.
Run: python3 qa_seed.py   (QA_BASE=http://127.0.0.1)
"""

import datetime as dt
import os

from qalib import Refused, Results, Session

R = Results()
OWNER = ("owner@qa-pharmacy.test", "QaOwner-2026!")
STAFF_PWD = "QaStaff-2026!"
STAFF = [
	# email, name, role profile, gender
	("manager@qa-pharmacy.test", "QA Manager Omar", "Pharmacy Manager", "Male"),
	("pharmacist@qa-pharmacy.test", "QA Pharmacist Huda", "Pharmacist", "Female"),
	("cashier@qa-pharmacy.test", "QA Cashier Sara", "Cashier", "Female"),
	("cashier2@qa-pharmacy.test", "QA Cashier Ali", "Cashier", "Male"),
	("purchasing@qa-pharmacy.test", "QA Purchasing Karim", "Purchasing Officer", "Male"),
	("stock@qa-pharmacy.test", "QA Stock Zainab", "Inventory Manager", "Female"),
	("accountant@qa-pharmacy.test", "QA Accountant Noor", "Pharmacy Accountant", "Female"),
]
GROUPS = [
	("Analgesics", "مسكنات"),
	("Antibiotics", "مضادات حيوية"),
	("Vitamins & Supplements", "فيتامينات ومكملات"),
	("Gastrointestinal", "أدوية الجهاز الهضمي"),
	("Respiratory & Allergy", "أدوية التنفس والحساسية"),
	("Chronic Care", "الأمراض المزمنة"),
	("Personal Care", "العناية الشخصية"),
]
# code, English, Arabic, generic, group, barcodes, price, cost, reorder level
MEDICINES = [
	("QA-PAN500", "Panadol 500mg Tablets", "بنادول 500 ملغ أقراص", "Paracetamol", "Analgesics", ["6291100000011", "5000158062474"], 2500, 1500, 20),
	("QA-PANEXT", "Panadol Extra Tablets", "بنادول إكسترا أقراص", "Paracetamol + Caffeine", "Analgesics", ["6291100000028"], 3500, 2200, 10),
	("QA-BRUF400", "Brufen 400mg Tablets", "بروفين 400 ملغ أقراص", "Ibuprofen", "Analgesics", ["6291100000035"], 3000, 1800, 10),
	("QA-VOLT50", "Voltaren 50mg Tablets", "فولتارين 50 ملغ أقراص", "Diclofenac", "Analgesics", ["6291100000042"], 4500, 2900, 5),
	("QA-AUG1G", "Augmentin 1g Tablets", "أوغمنتين 1 غم أقراص", "Amoxicillin + Clavulanate", "Antibiotics", ["6291100000059"], 9000, 6000, 10),
	("QA-AMOXSYR", "Amoxil 250mg/5ml Suspension", "أموكسيل 250 ملغ شراب", "Amoxicillin", "Antibiotics", ["6291100000066"], 5000, 3200, 5),
	("QA-AZI500", "Azithromycin 500mg Tablets", "أزيثرومايسين 500 ملغ", "Azithromycin", "Antibiotics", ["6291100000073"], 7500, 4800, 5),
	("QA-CIPRO500", "Ciprofloxacin 500mg Tablets", "سيبروفلوكساسين 500 ملغ", "Ciprofloxacin", "Antibiotics", ["6291100000080"], 6000, 3600, 5),
	("QA-VITC1000", "Vitamin C 1000mg Effervescent", "فيتامين سي 1000 ملغ فوار", "Ascorbic Acid", "Vitamins & Supplements", ["6291100000097"], 4000, 2400, 10),
	("QA-VITD3", "Vitamin D3 5000 IU Capsules", "فيتامين د3 5000 وحدة", "Cholecalciferol", "Vitamins & Supplements", ["6291100000103"], 8000, 5000, 5),
	("QA-OMEP20", "Omeprazole 20mg Capsules", "أوميبرازول 20 ملغ كبسول", "Omeprazole", "Gastrointestinal", ["6291100000110"], 3500, 2000, 10),
	("QA-GAVISCON", "Gaviscon Liquid 200ml", "غافيسكون شراب 200 مل", "Sodium alginate", "Gastrointestinal", ["6291100000127"], 6500, 4200, 5),
	("QA-ZYRTEC", "Zyrtec 10mg Tablets", "زيرتك 10 ملغ أقراص", "Cetirizine", "Respiratory & Allergy", ["6291100000134"], 5500, 3500, 5),
	("QA-VENTOLIN", "Ventolin Inhaler 100mcg", "فنتولين بخاخ 100 مكغ", "Salbutamol", "Respiratory & Allergy", ["6291100000141"], 7000, 4500, 5),
	("QA-GLUCO500", "Glucophage 500mg Tablets", "جلوكوفاج 500 ملغ أقراص", "Metformin", "Chronic Care", ["6291100000158"], 4000, 2500, 10),
	("QA-CONCOR5", "Concor 5mg Tablets", "كونكور 5 ملغ أقراص", "Bisoprolol", "Chronic Care", ["6291100000165"], 6000, 3900, 5),
	("QA-LIPITOR20", "Lipitor 20mg Tablets", "ليبيتور 20 ملغ أقراص", "Atorvastatin", "Chronic Care", ["6291100000172"], 12000, 8000, 5),
	("QA-NIVEA", "Nivea Soft Cream 200ml", "كريم نيفيا سوفت 200 مل", None, "Personal Care", ["6291100000189"], 5000, 3000, 3),
	("QA-SENSO", "Sensodyne Toothpaste 75ml", "معجون سنسوداين 75 مل", None, "Personal Care", ["6291100000196"], 4500, 2800, 3),
	("QA-INSULIN", "Insulin Glargine Pen (cold chain)", "قلم إنسولين غلارجين", "Insulin glargine", "Chronic Care", ["6291100000202"], 25000, 18000, 2),
]
SUPPLIERS = [("QA Baghdad Pharma Supply", "07701111111"), ("QA Basra Medical Trading", "07802222222")]
today = dt.date.today()


def d(days: int) -> str:
	return str(today + dt.timedelta(days=days))


def ensure(session, doctype, filters, doc):
	rows = session.get_list(doctype, filters=filters, fields=["name"], limit=1)
	return rows[0]["name"] if rows else session.insert({"doctype": doctype, **doc})["name"]


def main():
	owner = Session("owner").login(*OWNER)
	me = owner.call("frappe.auth.get_logged_user")
	R.add("A5 QA pharmacy", "owner signs in", "the owner created by setup can sign in", me, me == OWNER[0])
	company = owner.value("Global Defaults", "Global Defaults", "default_company")
	branch = owner.get_list("Branch", fields=["name", "pharmacyos_warehouse"], limit=1)[0]
	wh = branch["pharmacyos_warehouse"]
	ctx = {"company": company, "branch": branch["name"], "warehouse": wh}

	# ---- staff: users with role profiles, and employee records linked to them
	for email, name, profile, gender in STAFF:
		if not owner.get_list("User", filters=[["name", "=", email]], fields=["name"], limit=1):
			first, _, last = name.partition(" ")
			owner.insert({"doctype": "User", "email": email, "first_name": name, "role_profile_name": profile, "new_password": STAFF_PWD, "send_welcome_email": 0, "language": "ar"})
	employees(owner, company, branch)

	# ---- catalogue (owner): categories and medicines
	for en, ar in GROUPS:
		ensure(owner, "Item Group", [["name", "=", en]], {"item_group_name": en, "parent_item_group": "All Item Groups", "is_group": 0})
	purch = Session("purchasing").login("purchasing@qa-pharmacy.test", STAFF_PWD)
	for name, phone in SUPPLIERS:
		sg = purch.get_list("Supplier Group", filters=[["is_group", "=", 0]], fields=["name"], limit=1)[0]["name"]
		ensure(purch, "Supplier", [["supplier_name", "=", name]], {"supplier_name": name, "supplier_group": sg, "mobile_no": phone})
	sups = purch.get_list("Supplier", filters=[["supplier_name", "like", "QA %"]], fields=["name", "owner"])
	R.add("A6 Purchasing", "supplier creation by the Purchasing Officer", "2 suppliers created by purchasing@", sups, len(sups) == 2 and all(s["owner"] == "purchasing@qa-pharmacy.test" for s in sups))

	for code, en, ar, generic, group, barcodes, price, cost, reorder in MEDICINES:
		if not owner.get_list("Item", filters=[["name", "=", code]], fields=["name"], limit=1):
			owner.call("pharmacyos_erp.pharmacy.medicine.create_medicine", item_code=code, item_name=en, pharma_name_ar=ar, pharma_generic_name=generic,
				item_group=group, stock_uom="Nos", barcode=barcodes[0], standard_rate=price, buying_rate=cost,
				reorder_warehouse=wh, reorder_level=reorder, reorder_qty=reorder * 3, default_supplier=SUPPLIERS[0][0], active_ingredient=None)
			if len(barcodes) > 1:
				item = owner.get_doc("Item", code)
				item["barcodes"] += [{"barcode": b} for b in barcodes[1:]]
				owner.update("Item", code, {"barcodes": item["barcodes"]})
	items = owner.get_list("Item", filters=[["name", "like", "QA-%"]], fields=["name", "item_group", "has_batch_no", "has_expiry_date"])
	R.add("A5 QA pharmacy", "20 medicines in 7 categories, batch + expiry tracked", "20 items, all has_batch_no and has_expiry_date", f"{len(items)} items, groups={sorted({i['item_group'] for i in items})}", len(items) == 20 and all(i["has_batch_no"] and i["has_expiry_date"] for i in items))
	pan = owner.get_doc("Item", "QA-PAN500")
	R.add("A5 QA pharmacy", "multiple barcodes on one medicine", "Panadol 500 has 2 barcodes", [b["barcode"] for b in pan["barcodes"]], len(pan["barcodes"]) == 2)

	purchasing(purch, owner, ctx)
	return ctx


def employees(owner, company, branch):
	try:
		for email, name, profile, gender in STAFF:
			ensure(owner, "Employee", [["user_id", "=", email]], {
				"first_name": name, "gender": gender, "date_of_birth": "1990-01-01", "date_of_joining": d(-400),
				"company": company, "status": "Active", "user_id": email, "branch": branch["name"],
			})
	except Refused as e:
		return R.add("A5 QA pharmacy", "owner creates employee records linked to staff accounts", "7 employees linked to the staff users", f"refused HTTP {e.status}: {e.message}", False)
	emps = owner.get_list("Employee", filters=[["user_id", "like", "%qa-pharmacy.test"]], fields=["name", "employee_name", "user_id", "owner"])
	return R.add("A5 QA pharmacy", "owner creates employee records linked to staff accounts", "7 employees linked to the staff users", [(e["employee_name"], e["user_id"]) for e in emps], len(emps) == len(STAFF))


def receipt_from_po(s, po, lines, wh):
	"""Receive `lines` {item_code: [(batch_id, expiry, qty), ...]} against the PO (partial or full)."""
	pr = s.call("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", source_name=po)
	rows = []
	for row in pr["items"]:
		for batch_id, exp, qty in lines.get(row["item_code"], []):
			b = s.call("pharmacyos_erp.pharmacy.receiving.create_receiving_batch", item_code=row["item_code"], batch_id=batch_id, expiry_date=exp, supplier=pr["supplier"])
			rows.append({**row, "qty": qty, "received_qty": qty, "stock_qty": qty, "batch_no": b["name"], "use_serial_batch_fields": 1, "warehouse": wh, "name": None, "__islocal": 1})
	pr["items"] = rows
	pr.pop("name", None)
	doc = s.insert(pr)
	return s.submit(doc)["name"]


def direct_receipt(s, supplier, company, wh, lines, posting=None):
	doc = {"doctype": "Purchase Receipt", "supplier": supplier, "company": company, "set_warehouse": wh, "items": []}
	if posting:
		doc.update({"set_posting_time": 1, "posting_date": posting})
	for code, batch_id, exp, qty, rate in lines:
		b = s.call("pharmacyos_erp.pharmacy.receiving.create_receiving_batch", item_code=code, batch_id=batch_id, expiry_date=exp, supplier=supplier)
		doc["items"].append({"item_code": code, "qty": qty, "rate": rate, "warehouse": wh, "batch_no": b["name"], "use_serial_batch_fields": 1})
	return s.submit(s.insert(doc))["name"]


def batch_exists(s, batch_id):
	return bool(s.get_list("Batch", filters=[["batch_id", "=", batch_id]], fields=["name"], limit=1))


def purchasing(purch, owner, ctx):
	"""Resumable: each step runs once (a step already done on this server is skipped)."""
	wh, company = ctx["warehouse"], ctx["company"]
	sup1, sup2 = SUPPLIERS[0][0], SUPPLIERS[1][0]
	cost = {m[0]: m[7] for m in MEDICINES}

	def make_po(supplier, lines):
		existing = purch.get_list("Purchase Order", filters=[["supplier", "=", supplier], ["docstatus", "=", 1]], fields=["name"], limit=1)
		if existing:
			return existing[0]["name"]
		name = purch.call("pharmacyos_erp.pharmacy.purchasing.make_purchase_order", supplier=supplier, items=[{"item_code": c, "qty": q, "warehouse": wh} for c, q in lines])
		doc = purch.get_doc("Purchase Order", name)
		for row in doc["items"]:
			row["rate"] = cost[row["item_code"]]
		purch.submit(purch.update("Purchase Order", name, {"items": doc["items"]}))
		return name

	# PO 1: 8 medicines from supplier 1
	po1 = make_po(sup1, [("QA-PAN500", 100), ("QA-PANEXT", 40), ("QA-BRUF400", 50), ("QA-AUG1G", 30), ("QA-AZI500", 20), ("QA-OMEP20", 40), ("QA-ZYRTEC", 30), ("QA-GLUCO500", 60)])
	R.add("A6 Purchasing", "purchase order created and submitted by the Purchasing Officer", "PO with 8 lines, submitted, owner purchasing@", (po1, purch.value("Purchase Order", po1, "owner")), purch.value("Purchase Order", po1, "docstatus") == 1 and purch.value("Purchase Order", po1, "owner") == "purchasing@qa-pharmacy.test")

	if not batch_exists(purch, "PAN-QA-2601"):
		# partial receipt: half of Panadol (batch A), all Brufen, half Augmentin, all Omeprazole
		pr1 = receipt_from_po(purch, po1, {
			"QA-PAN500": [("PAN-QA-2601", d(540), 50)],
			"QA-BRUF400": [("BRU-QA-2601", d(400), 50)],
			"QA-AUG1G": [("AUG-QA-2601", d(300), 15)],
			"QA-OMEP20": [("OME-QA-2601", d(365), 40)],
		}, wh)
		po = purch.get_doc("Purchase Order", po1)
		R.add("A6 Purchasing", "partial receiving against the PO", "PO partly received (0 < per_received < 100)", {"receipt": pr1, "per_received": po["per_received"], "status": po["status"]}, 0 < po["per_received"] < 100)
	pr1 = purch.get_list("Purchase Receipt Item", filters=[["purchase_order", "=", po1], ["item_code", "=", "QA-BRUF400"]], fields=["parent"], limit=1) if False else None
	pr1 = owner.get_list("Purchase Receipt", filters=[["supplier", "=", sup1], ["docstatus", "=", 1], ["is_return", "=", 0]], fields=["name"], order_by="creation asc", limit=1)[0]["name"]

	if not batch_exists(purch, "PAN-QA-2602"):
		# the rest: Panadol second batch with a LATER expiry, Augmentin second batch with an EARLIER expiry (FEFO)
		pr2 = receipt_from_po(purch, po1, {
			"QA-PAN500": [("PAN-QA-2602", d(720), 50)],
			"QA-PANEXT": [("PEX-QA-2601", d(500), 40)],
			"QA-AUG1G": [("AUG-QA-2600", d(120), 15)],
			"QA-AZI500": [("AZI-QA-2601", d(450), 20)],
			"QA-ZYRTEC": [("ZYR-QA-2601", d(600), 30)],
			"QA-GLUCO500": [("GLU-QA-2601", d(700), 60)],
		}, wh)
		po = purch.get_doc("Purchase Order", po1)
		R.add("A6 Purchasing", "full receiving completes the PO", "per_received = 100, status To Bill", {"receipt": pr2, "per_received": po["per_received"], "status": po["status"]}, po["per_received"] >= 100)

	# PO 2: supplier 2, received in one go
	po2 = make_po(sup2, [("QA-VITC1000", 30), ("QA-VITD3", 20), ("QA-GAVISCON", 15), ("QA-VENTOLIN", 12), ("QA-CONCOR5", 25), ("QA-LIPITOR20", 20), ("QA-NIVEA", 10), ("QA-SENSO", 12), ("QA-CIPRO500", 3)])
	if not batch_exists(purch, "VTC-QA-2601"):
		pr3 = receipt_from_po(purch, po2, {
			"QA-VITC1000": [("VTC-QA-2601", d(330), 30)],
			"QA-VITD3": [("VTD-QA-2601", d(800), 20)],
			"QA-GAVISCON": [("GAV-QA-2601", d(250), 15)],
			"QA-VENTOLIN": [("VEN-QA-2601", d(500), 12)],
			"QA-CONCOR5": [("CON-QA-2601", d(640), 25)],
			"QA-LIPITOR20": [("LIP-QA-2601", d(560), 20)],
			"QA-NIVEA": [("NIV-QA-2601", d(900), 10)],
			"QA-SENSO": [("SEN-QA-2601", d(850), 12)],
			"QA-CIPRO500": [("CIP-QA-2601", d(480), 3)],  # below its reorder level of 5: low stock
		}, wh)
		R.add("A6 Purchasing", "second supplier PO received in full", "PO2 100% received", pr3, purch.get_doc("Purchase Order", po2)["per_received"] >= 100)

	if not batch_exists(purch, "AMX-QA-2509"):
		# older deliveries (received months ago while valid): an Amoxil batch that has since EXPIRED and a
		# Voltaren batch that is NEAR EXPIRY, plus a fresher Amoxil batch
		pr4 = direct_receipt(purch, sup2, company, wh, [
			("QA-AMOXSYR", "AMX-QA-2509", d(-6), 8, cost["QA-AMOXSYR"]),
			("QA-VOLT50", "VOL-QA-2510", d(25), 10, cost["QA-VOLT50"]),
		], posting=d(-200))
		pr5 = direct_receipt(purch, sup2, company, wh, [("QA-AMOXSYR", "AMX-QA-2604", d(400), 12, cost["QA-AMOXSYR"])])
		R.add("A6 Purchasing", "back-dated delivery (now expired / near-expiry batches) + fresh batch", "both receipts submitted (the first dated 200 days ago)",
			[(pr, purch.value("Purchase Receipt", pr, "docstatus"), purch.value("Purchase Receipt", pr, "posting_date")) for pr in (pr4, pr5)],
			purch.value("Purchase Receipt", pr4, "docstatus") == 1 and purch.value("Purchase Receipt", pr5, "docstatus") == 1 and purch.value("Purchase Receipt", pr4, "posting_date") == d(-200))
		# receiving an already-expired batch today is refused
		R.refused("A6 Purchasing", "receiving an expired batch today is refused", "server refuses an expired batch on today's receipt",
			lambda: direct_receipt(purch, sup2, company, wh, [("QA-AMOXSYR", "AMX-QA-EXPIRED", d(-30), 5, cost["QA-AMOXSYR"])]), must_contain="expire")
	# QA-INSULIN is never received: out of stock

	if not owner.get_list("Purchase Receipt", filters=[["supplier", "=", sup1], ["is_return", "=", 1], ["docstatus", "=", 1]], fields=["name"], limit=1):
		# purchase return: 2 Brufen back to supplier 1 from the partial receipt's batch
		ret = purch.call("erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_return", source_name=pr1)
		ret["items"] = [dict(r, qty=-2, received_qty=-2, stock_qty=-2) for r in ret["items"] if r["item_code"] == "QA-BRUF400"]
		ret.pop("name", None)
		before = brufen_batch_qty(owner)
		retname = purch.submit(purch.insert(ret))["name"]
		after = brufen_batch_qty(owner)
		R.add("A6 Purchasing", "purchase return to supplier takes stock out of the received batch", "BRU-QA-2601 goes 50 → 48", {"return": retname, "before": before, "after": after}, before - after == 2)

	# cost / valuation of a received batch equals the PO rate; stock per batch equals what was received
	sle = owner.get_list("Stock Ledger Entry", filters=[["voucher_no", "=", pr1], ["item_code", "=", "QA-PAN500"]], fields=["incoming_rate", "actual_qty", "valuation_rate"])
	R.add("A6 Purchasing", "received cost becomes the stock valuation", "incoming rate 1500 for Panadol", sle, bool(sle) and abs(sle[0]["incoming_rate"] - 1500) < 0.01)
	pan = {b: batch_qty(owner, b) for b in ("PAN-QA-2601", "PAN-QA-2602")}
	R.add("A6 Purchasing", "two batches of one medicine with different expiry", "Panadol 50 + 50 in two batches", pan, pan == {"PAN-QA-2601": 50, "PAN-QA-2602": 50})
	bins = owner.get_list("Bin", filters=[["item_code", "like", "QA-%"], ["warehouse", "=", wh]], fields=["item_code", "actual_qty"], limit=100)
	R.add("A6 Purchasing", "inventory after purchasing (Bin = received − returned)", "Brufen 48, Panadol 100, Insulin none", {b["item_code"]: b["actual_qty"] for b in bins}, {b["item_code"]: b["actual_qty"] for b in bins}.get("QA-BRUF400") == 48)


def batch_qty(session, batch_id, search=None):
	rows = session.get_call("pharmacyos_erp.pharmacy.expiry.get_batches", bucket="all", search=search or batch_id, page_length=200)["rows"]
	return sum(r["qty"] for r in rows if r["batch_id"] == batch_id)


def brufen_batch_qty(owner):
	return batch_qty(owner, "BRU-QA-2601")


if __name__ == "__main__":
	ctx = main()
	print(ctx)
