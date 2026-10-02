"""Fictitious development data for PharmacyOS (developer mode only).

	bench --site pharmacyos.localhost execute pharmacyos_erp.setup.demo_data.create_demo_data

Everything is created through normal ERPNext documents (Items, Batches, submitted Purchase Receipts,
Sales Invoices, a Purchase Order), so stock, valuation and GL entries are real ledger results — the
dashboards built on them show genuine numbers for this test data. All names are fictitious and
marked "Demo"/"Test". Never run on a production site; the function refuses unless developer mode
is enabled.
"""

import frappe
from frappe.utils import add_days, getdate, nowdate

SUPPLIERS = ["Demo Pharma Distributor", "Test Medical Supplies"]

INGREDIENTS = [
	("Paracetamol", "باراسيتامول", "N02BE01"),
	("Amoxicillin", "أموكسيسيلين", "J01CA04"),
	("Ibuprofen", "إيبوبروفين", "M01AE01"),
	("Omeprazole", "أوميبرازول", "A02BC01"),
	("Metformin", "ميتفورمين", "A10BA02"),
	("Cetirizine", "سيتريزين", "R06AE07"),
	("Salbutamol", "سالبيوتامول", "R03AC02"),
	("Azithromycin", "أزيثرومايسين", "J01FA10"),
]

# (code, commercial name, arabic, generic, strength, form, ingredient, sell, buy, dispensing, supplier idx)
MEDICINES = [
	(
		"DEMO-PARA-500",
		"Demo Paracetamol 500 mg Tablets",
		"باراسيتامول تجريبي ٥٠٠ ملغ أقراص",
		"Paracetamol",
		"500 mg",
		"Tablet",
		"Paracetamol",
		2500,
		1500,
		"Over the Counter",
		0,
	),
	(
		"DEMO-AMOX-500",
		"Demo Amoxicillin 500 mg Capsules",
		"أموكسيسيلين تجريبي ٥٠٠ ملغ كبسول",
		"Amoxicillin",
		"500 mg",
		"Capsule",
		"Amoxicillin",
		6000,
		3800,
		"Prescription Required",
		0,
	),
	(
		"DEMO-IBU-400",
		"Demo Ibuprofen 400 mg Tablets",
		"إيبوبروفين تجريبي ٤٠٠ ملغ أقراص",
		"Ibuprofen",
		"400 mg",
		"Tablet",
		"Ibuprofen",
		3000,
		1750,
		"Over the Counter",
		1,
	),
	(
		"DEMO-OME-20",
		"Demo Omeprazole 20 mg Capsules",
		"أوميبرازول تجريبي ٢٠ ملغ كبسول",
		"Omeprazole",
		"20 mg",
		"Capsule",
		"Omeprazole",
		5000,
		2900,
		"Over the Counter",
		1,
	),
	(
		"DEMO-MET-850",
		"Demo Metformin 850 mg Tablets",
		"ميتفورمين تجريبي ٨٥٠ ملغ أقراص",
		"Metformin",
		"850 mg",
		"Tablet",
		"Metformin",
		4000,
		2200,
		"Prescription Required",
		0,
	),
	(
		"DEMO-CET-SYR",
		"Demo Cetirizine 5 mg/5 ml Syrup",
		"سيتريزين تجريبي شراب",
		"Cetirizine",
		"5 mg/5 ml",
		"Syrup",
		"Cetirizine",
		4500,
		2600,
		"Over the Counter",
		1,
	),
	(
		"DEMO-SAL-INH",
		"Demo Salbutamol 100 mcg Inhaler",
		"سالبيوتامول تجريبي بخاخ",
		"Salbutamol",
		"100 mcg/dose",
		"Inhaler",
		"Salbutamol",
		9000,
		6100,
		"Prescription Required",
		0,
	),
	(
		"DEMO-AZI-250",
		"Demo Azithromycin 250 mg Tablets",
		"أزيثرومايسين تجريبي ٢٥٠ ملغ أقراص",
		"Azithromycin",
		"250 mg",
		"Tablet",
		"Azithromycin",
		8000,
		5200,
		"Prescription Required",
		1,
	),
]

# (item code, batch_id, days to expiry from today, qty, branch idx)
BATCHES = [
	("DEMO-PARA-500", "PA-2401", -20, 40, 0),
	("DEMO-PARA-500", "PA-2507", 25, 120, 0),
	("DEMO-PARA-500", "PA-2612", 420, 300, 0),
	("DEMO-PARA-500", "PA-2611", 380, 80, 1),
	("DEMO-AMOX-500", "AX-2502", 12, 30, 0),
	("DEMO-AMOX-500", "AX-2603", 300, 60, 1),
	("DEMO-IBU-400", "IB-2505", 48, 90, 0),
	("DEMO-IBU-400", "IB-2604", 520, 50, 1),
	("DEMO-OME-20", "OM-2409", -5, 15, 1),
	("DEMO-OME-20", "OM-2608", 610, 40, 0),
	("DEMO-MET-850", "MF-2511", 75, 8, 0),
	("DEMO-CET-SYR", "CT-2506", 33, 25, 0),
	("DEMO-CET-SYR", "CT-2607", 365, 20, 1),
	("DEMO-SAL-INH", "SB-2610", 700, 6, 0),
	("DEMO-AZI-250", "AZ-2508", 85, 12, 1),
]

BRANCHES = [
	("Demo Branch Al-Mansour", "فرع المنصور التجريبي", "MAN"),
	("Demo Branch Al-Karrada", "فرع الكرادة التجريبي", "KAR"),
]


def create_demo_data():
	if not frappe.conf.developer_mode:
		frappe.throw("Demo data can only be created on a developer-mode site.")
	if frappe.db.exists("Supplier", SUPPLIERS[0]):
		print("Demo data already present; nothing to do.")
		return

	company = frappe.defaults.get_global_default("company")
	abbr = frappe.get_cached_value("Company", company, "abbr")
	parent_wh = frappe.db.get_value(
		"Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ["is", "not set"]}
	)

	settings = frappe.get_single("PharmacyOS Settings")
	settings.pharmacy_name = "PharmacyOS Demo Pharmacy"
	settings.pharmacy_name_ar = "صيدلية PharmacyOS التجريبية"
	settings.receipt_footer = "Demo data — not a real pharmacy."
	settings.save()

	for name in SUPPLIERS:
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": name,
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}),
			}
		).insert()
	for ing, ar, atc in INGREDIENTS:
		if not frappe.db.exists("Active Ingredient", ing):
			frappe.get_doc(
				{
					"doctype": "Active Ingredient",
					"ingredient_name": ing,
					"ingredient_name_ar": ar,
					"atc_code": atc,
				}
			).insert()

	warehouses = []
	for branch, branch_ar, code in BRANCHES:
		wh = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": branch,
				"company": company,
				"parent_warehouse": parent_wh,
			}
		).insert()
		warehouses.append(wh.name)
		frappe.get_doc(
			{
				"doctype": "Branch",
				"branch": branch,
				"pharmacyos_warehouse": wh.name,
				"pharmacyos_branch_name_ar": branch_ar,
				"pharmacyos_storefront_code": f"DEMO-{code}",
			}
		).insert()

	from pharmacyos_erp.pharmacy.medicine import create_medicine

	for code, name, ar, generic, strength, form, ing, sell, buy, disp, sup in MEDICINES:
		create_medicine(
			item_code=code,
			item_name=name,
			item_group="Products",
			stock_uom="Nos",
			pharma_name_ar=ar,
			pharma_generic_name=generic,
			pharma_strength=strength,
			pharma_dosage_form=form,
			active_ingredient=ing,
			standard_rate=sell,
			buying_rate=buy,
			pharma_dispensing=disp,
			default_supplier=SUPPLIERS[sup],
			reorder_warehouse=warehouses[0],
			reorder_level=50
			if code in ("DEMO-MET-850", "DEMO-SAL-INH", "DEMO-AMOX-500", "DEMO-AZI-250")
			else 10,
			reorder_qty=100,
		)
		frappe.db.set_value("Item", code, "pharmacyos_publish", 1)

	# Receive stock: one Purchase Receipt per batch, posted well before each batch's expiry.
	rates = {m[0]: m[8] for m in MEDICINES}
	supplier_of = {m[0]: SUPPLIERS[m[10]] for m in MEDICINES}
	for code, batch_id, days, qty, wh_idx in BATCHES:
		expiry = add_days(nowdate(), days)
		received = min(add_days(nowdate(), -30), add_days(expiry, -200))
		batch = frappe.get_doc(
			{
				"doctype": "Batch",
				"batch_id": batch_id,
				"item": code,
				"expiry_date": expiry,
				"manufacturing_date": add_days(received, -60),
			}
		).insert()
		pr = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": supplier_of[code],
				"company": company,
				"set_posting_time": 1,
				"posting_date": received,
				"set_warehouse": warehouses[wh_idx],
				"items": [
					{
						"item_code": code,
						"qty": qty,
						"rate": rates[code],
						"warehouse": warehouses[wh_idx],
						"batch_no": batch.name,
						"use_serial_batch_fields": 1,
					}
				],
			}
		)
		pr.insert()
		pr.submit()
		frappe.db.set_value("Batch", batch.name, "supplier", supplier_of[code])

	# Sales over the last week (FEFO batch chosen from ERPNext's expiry ordering).
	from pharmacyos_erp.pharmacy.fefo import get_fefo_available

	customer = (
		frappe.db.get_value("Customer", {})
		or frappe.get_doc(
			{"doctype": "Customer", "customer_name": "Walk-in Customer (Demo)", "customer_type": "Individual"}
		)
		.insert()
		.name
	)
	sells = {m[0]: m[7] for m in MEDICINES}
	plan = [
		(0, "DEMO-PARA-500", 6),
		(0, "DEMO-IBU-400", 3),
		(0, "DEMO-CET-SYR", 2),
		(0, "DEMO-AMOX-500", 2),
		(-1, "DEMO-PARA-500", 10),
		(-1, "DEMO-OME-20", 4),
		(-2, "DEMO-IBU-400", 5),
		(-3, "DEMO-PARA-500", 8),
		(-4, "DEMO-CET-SYR", 3),
		(-5, "DEMO-OME-20", 2),
		(-6, "DEMO-PARA-500", 12),
	]
	for day, code, qty in plan:
		available = get_fefo_available(code, warehouses[0])
		if not available:
			continue
		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"customer": customer,
				"company": company,
				"set_posting_time": 1,
				"posting_date": add_days(nowdate(), day),
				"due_date": add_days(nowdate(), day),
				"update_stock": 1,
				"set_warehouse": warehouses[0],
				"items": [
					{
						"item_code": code,
						"qty": qty,
						"rate": sells[code],
						"warehouse": warehouses[0],
						"batch_no": available[0]["batch_no"],
						"use_serial_batch_fields": 1,
					}
				],
			}
		)
		si.insert()
		si.submit()

	# One open purchase order awaiting delivery.
	po = frappe.get_doc(
		{
			"doctype": "Purchase Order",
			"supplier": SUPPLIERS[0],
			"company": company,
			"transaction_date": nowdate(),
			"schedule_date": add_days(nowdate(), 5),
			"items": [
				{
					"item_code": "DEMO-MET-850",
					"qty": 100,
					"rate": rates["DEMO-MET-850"],
					"warehouse": warehouses[0],
					"schedule_date": add_days(nowdate(), 5),
				}
			],
		}
	).insert()
	po.submit()
	frappe.db.commit()
	print(f"Demo data created for {company} ({abbr}).")
