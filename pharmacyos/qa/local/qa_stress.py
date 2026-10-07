"""A22: practical stress — 100 rapid scans, 120 extra products, 60 sequential sales, 100 searches, a
populated report, and stock/ledger consistency afterwards. Timings are recorded (p50 / p95 / max).

QA-only. Run after qa_ops.py (the cashier opens a new shift for this).
"""

import datetime as dt
import json
import statistics
import time

from qalib import Results, Session, rid
from qa_seed import OWNER, STAFF_PWD

R = Results()
POS = "pharmacyos_erp.pos.api."
PROFILE = "Main Counter"
A = "A22 Stress"


def timed(fn):
	t = time.perf_counter()
	out = fn()
	return out, (time.perf_counter() - t) * 1000


def stats(ms):
	ms = sorted(ms)
	return {"n": len(ms), "p50_ms": round(statistics.median(ms)), "p95_ms": round(ms[int(len(ms) * 0.95) - 1]), "max_ms": round(ms[-1])}


def main():
	owner = Session("owner").login(*OWNER)
	purch = Session("purchasing").login("purchasing@qa-pharmacy.test", STAFF_PWD)
	cashier = Session("cashier").login("cashier@qa-pharmacy.test", STAFF_PWD)
	wh = owner.get_list("Branch", fields=["pharmacyos_warehouse"], limit=1)[0]["pharmacyos_warehouse"]
	company = owner.value("Global Defaults", "Global Defaults", "default_company")
	today = dt.date.today()

	# 120 extra products, each received with one batch
	have = {r["name"] for r in owner.get_list("Item", filters=[["name", "like", "QA-STRESS-%"]], fields=["name"], limit=500)}
	times = []
	for i in range(120):
		code = f"QA-STRESS-{i:03d}"
		if code in have:
			continue
		_, ms = timed(lambda: owner.call("pharmacyos_erp.pharmacy.medicine.create_medicine", item_code=code, item_name=f"QA Stress Medicine {i:03d}", pharma_name_ar=f"دواء اختبار {i:03d}", item_group="Analgesics", stock_uom="Nos", barcode=f"62919{i:08d}", standard_rate=1000 + i * 10, buying_rate=600 + i * 5))
		times.append(ms)
	if times:
		R.add(A, "create 120 products", "all created", stats(times), True)
	if not owner.get_list("Purchase Receipt", filters=[["supplier", "=", "QA Basra Medical Trading"], ["remarks", "=", "QA stress stock"]], fields=["name"]):
		doc = {"doctype": "Purchase Receipt", "supplier": "QA Basra Medical Trading", "company": company, "set_warehouse": wh, "remarks": "QA stress stock", "items": []}
		for i in range(120):
			b = purch.call("pharmacyos_erp.pharmacy.receiving.create_receiving_batch", item_code=f"QA-STRESS-{i:03d}", batch_id=f"STR-{i:03d}", expiry_date=str(today + dt.timedelta(days=300 + i)))
			doc["items"].append({"item_code": f"QA-STRESS-{i:03d}", "qty": 50, "rate": 600 + i * 5, "warehouse": wh, "batch_no": b["name"], "use_serial_batch_fields": 1})
		(_, ms) = timed(lambda: purch.submit(purch.insert(doc)))
		R.add(A, "one purchase receipt with 120 lines (120 batches)", "submitted", {"ms": round(ms)}, True)
	total_items = len(owner.get_list("Item", fields=["name"], limit=1000))
	R.add(A, "catalogue size", "≥ 140 products", total_items, total_items >= 140)

	ctx = cashier.call(POS + "get_context")
	if ctx.get("shift"):
		prof = ctx["shift"]["pos_profile"]  # the cashier's open shift (sales go to the user's own shift)
	else:
		prof = PROFILE
		cashier.call("erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher", pos_profile=prof, company=company, balance_details=json.dumps([{"mode_of_payment": "Cash", "opening_amount": 25000}]))

	# 100 rapid scans (barcode lookups, back to back)
	ms = []
	misses = 0
	for i in range(100):
		res, t = timed(lambda: cashier.call(POS + "search_items", pos_profile=prof, search_term=f"62919{i % 120:08d}"))
		ms.append(t)
		misses += 0 if res["items"] and res["items"][0]["item_code"] == f"QA-STRESS-{i % 120:03d}" else 1
	R.add(A, "100 rapid barcode scans", "every scan resolves to its product", {**stats(ms), "wrong_or_missing": misses}, misses == 0)

	# 100 searches (English, Arabic, generic, partial)
	terms = ["panadol", "بنادول", "paracetamol", "stress medicine 0", "دواء اختبار", "omep", "ventolin", "فيتامين", "zyr", "QA-STRESS-11"]
	ms = []
	empty = 0
	for i in range(100):
		res, t = timed(lambda: cashier.call(POS + "search_items", pos_profile=prof, search_term=terms[i % len(terms)]))
		ms.append(t)
		empty += 0 if res["items"] else 1
	R.add(A, "100 searches (EN / AR / generic / partial)", "every search returns results", {**stats(ms), "empty": empty}, empty == 0)

	# 60 sequential sales of stress products, then stock consistency
	before = {f"QA-STRESS-{i:03d}": qty(owner, f"QA-STRESS-{i:03d}", wh) for i in range(20)}
	ms = []
	names = set()
	for i in range(60):
		code = f"QA-STRESS-{i % 20:03d}"
		out, t = timed(lambda: cashier.call(POS + "checkout", pos_profile=prof, items=[{"item_code": code, "qty": 1}], payments=[{"mode_of_payment": "Cash", "amount": 5000}], request_id=rid()))
		ms.append(t)
		names.add(out["name"])
	after = {c: qty(owner, c, wh) for c in before}
	sold = {c: before[c] - after[c] for c in before}
	R.add(A, "60 sequential sales", "60 distinct invoices", {**stats(ms), "invoices": len(names)}, len(names) == 60)
	R.add(A, "stock after 60 sales", "each of 20 products down by exactly 3", sold, all(v == 3 for v in sold.values()))

	# ledger consistency for every QA product: Bin = sum of stock ledger
	bad = []
	for item in owner.get_list("Item", filters=[["name", "like", "QA-%"]], fields=["name"], limit=1000):
		code = item["name"]
		b = qty(owner, code, wh)
		sle = owner.get_list("Stock Ledger Entry", filters=[["item_code", "=", code], ["warehouse", "=", wh], ["is_cancelled", "=", 0]], fields=[{"SUM": "actual_qty", "as": "q"}])
		s = (sle[0]["q"] if sle else 0) or 0
		if abs(b - s) > 1e-6:
			bad.append((code, b, s))
	R.add(A, "stock = stock ledger for every QA product", "no mismatch", bad or "all consistent", not bad)

	rep, t = timed(lambda: owner.call("pharmacyos_erp.pharmacy.reports.get_sales_report", from_date=str(today.replace(day=1)), to_date=str(today)))
	R.add(A, "month report with populated data", "answers in < 5 s", {"ms": round(t)}, t < 5000)
	return True


def qty(owner, code, wh):
	rows = owner.get_list("Bin", filters=[["item_code", "=", code], ["warehouse", "=", wh]], fields=["actual_qty"])
	return rows[0]["actual_qty"] if rows else 0


if __name__ == "__main__":
	main()
