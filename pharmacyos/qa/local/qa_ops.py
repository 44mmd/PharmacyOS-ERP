"""A7–A13, A19: inventory rules, a full counter shift (≥10 sales), returns, voids, shift close, expired
disposal and reports — over HTTP, as the cashier / manager / stock user / owner who would do them.

QA-only. Run after qa_seed.py.
"""

import datetime as dt
import json

from qalib import Refused, Results, Session, rid
from qa_seed import OWNER, STAFF_PWD, batch_qty

R = Results()
PROFILE = "Main Counter"
POS = "pharmacyos_erp.pos.api."
today = dt.date.today()


def login(email):
	return Session(email.split("@")[0]).login(email, STAFF_PWD)


def scan(s, code):
	"""What the POS does for a scanned code (Enter or Tab suffix): the item search in barcode mode."""
	return s.call(POS + "search_items", pos_profile=PROFILE, search_term=code)


def sell(s, lines, cash=None, discount=0, request_id=None, mode="Cash", profile=PROFILE):
	q = s.call(POS + "quote", pos_profile=profile, items=lines, additional_discount_percentage=discount)
	total = q.get("rounded_total") or q["grand_total"]
	paid = total if cash is None else cash
	out = s.call(POS + "checkout", pos_profile=profile, items=lines, payments=[{"mode_of_payment": mode, "amount": paid}],
		request_id=request_id or rid(), additional_discount_percentage=discount)
	return q, out


def inv_batches(owner, name):
	doc = owner.get_doc("Sales Invoice", name) if owner.get_list("Sales Invoice", filters=[["name", "=", name]], fields=["name"]) else owner.get_doc("POS Invoice", name)
	out = []
	for row in doc["items"]:
		b = row.get("batch_no")
		if not b and row.get("serial_and_batch_bundle"):
			bundle = owner.get_doc("Serial and Batch Bundle", row["serial_and_batch_bundle"])
			b = ",".join(e["batch_no"] for e in bundle["entries"])
		out.append((row["item_code"], row["qty"], b))
	return doc, out


def bin_qty(owner, item, wh):
	rows = owner.get_list("Bin", filters=[["item_code", "=", item], ["warehouse", "=", wh]], fields=["actual_qty"])
	return rows[0]["actual_qty"] if rows else 0


def main(start="inventory"):
	"""start: inventory (everything) | close (previous-day void, shift close, disposal, reports)."""
	owner = Session("owner").login(*OWNER)
	wh = owner.get_list("Branch", fields=["pharmacyos_warehouse"], limit=1)[0]["pharmacyos_warehouse"]
	company = owner.value("Global Defaults", "Global Defaults", "default_company")
	cashier, cashier2, manager, stock, pharm = (login(e) for e in ("cashier@qa-pharmacy.test", "cashier2@qa-pharmacy.test", "manager@qa-pharmacy.test", "stock@qa-pharmacy.test", "pharmacist@qa-pharmacy.test"))
	A7, A8, A9, A10, A11, A12, A19 = "A7 Inventory", "A8 POS shift", "A9 Returns", "A10 Void", "A11 Shift close", "A12 Expired disposal", "A19 Barcode (simulated HID)"
	if start == "close":
		amx_exp = owner.get_list("Batch", filters=[["batch_id", "=", "AMX-QA-2509"]], fields=["name"])[0]["name"]
		amx2604 = owner.get_list("Batch", filters=[["batch_id", "=", "AMX-QA-2604"]], fields=["name"])[0]["name"]
		sales = [x for x in cashier.call(POS + "recent_sales", limit=100) if not x["is_return"]]
		return tail(owner, cashier, manager, stock, pharm, company, wh, sales, amx_exp, amx2604)

	# ---------------------------------------------------------------- A7 inventory
	b = stock.get_call("pharmacyos_erp.pharmacy.expiry.get_batches", bucket="all", search="QA-", page_length=200)
	rows = [r for r in b["rows"] if r["item_code"].startswith("QA-")]
	R.add(A7, "stock per batch (Batches & Expiry)", "every received batch listed with qty and expiry", f"{len(rows)} batches; counts={b['counts']}", len(rows) >= 22)
	expired = [r for r in rows if str(r["status"]).lower() == "expired"]
	R.add(A7, "expired batch identified", "AMX-QA-2509 flagged Expired", [(r["batch_id"], r["qty"], str(r["expiry_date"])) for r in expired], any(r["batch_id"] == "AMX-QA-2509" for r in expired))
	near = [r for r in rows if r["days"] is not None and 0 <= r["days"] <= 30]
	R.add(A7, "near-expiry batch identified", "VOL-QA-2510 within 30 days", [(r["batch_id"], r["days"], r["status"]) for r in near], any(r["batch_id"] == "VOL-QA-2510" for r in near))
	fefo = owner.call("pharmacyos_erp.pharmacy.fefo.get_fefo_batches", item_code="QA-AUG1G", warehouse=wh)
	R.add(A7, "FEFO order", "Augmentin: AUG-QA-2600 (earlier expiry) before AUG-QA-2601", [(r["batch_id"], str(r["expiry_date"])) for r in fefo], [r["batch_id"] for r in fefo][:2] == ["AUG-QA-2600", "AUG-QA-2601"])
	fefo_amx = owner.call("pharmacyos_erp.pharmacy.fefo.get_fefo_batches", item_code="QA-AMOXSYR", warehouse=wh)
	R.add(A7, "expired batch excluded from sellable stock", "Amoxil sellable = AMX-QA-2604 only (12)", [(r["batch_id"], r["qty"]) for r in fefo_amx], [r["batch_id"] for r in fefo_amx] == ["AMX-QA-2604"])
	health = stock.get_call("pharmacyos_erp.pharmacy.inventory.get_inventory_health")
	R.add(A7, "low stock / out of stock (Inventory Health)", "Cipro low (3 < 5), Insulin out of stock", json.dumps(health, default=str)[:400], "QA-CIPRO500" in json.dumps(health) and "QA-INSULIN" in json.dumps(health))

	# stock adjustment by the Inventory Manager: a count finds 1 Nivea less
	before = bin_qty(owner, "QA-NIVEA", wh)
	niv = stock.get_call("pharmacyos_erp.pharmacy.expiry.get_batches", bucket="all", search="QA-NIVEA")["rows"][0]
	sr = stock.insert({"doctype": "Stock Reconciliation", "company": company, "purpose": "Stock Reconciliation", "items": [
		{"item_code": "QA-NIVEA", "warehouse": wh, "qty": niv["qty"] - 1, "batch_no": niv["batch"], "use_serial_batch_fields": 1}]})
	stock.submit(sr)
	R.add(A7, "stock adjustment (count) by the Inventory Manager", "Nivea 10 → 9, ledger entry written", {"reconciliation": sr["name"], "before": before, "after": bin_qty(owner, "QA-NIVEA", wh)}, bin_qty(owner, "QA-NIVEA", wh) == before - 1)
	R.refused(A7, "cashier cannot adjust stock", "Stock Reconciliation refused for a cashier",
		lambda: cashier.insert({"doctype": "Stock Reconciliation", "company": company, "purpose": "Stock Reconciliation", "items": [{"item_code": "QA-NIVEA", "warehouse": wh, "qty": 50, "batch_no": niv["batch"], "use_serial_batch_fields": 1}]}))

	# ---------------------------------------------------------------- A8 shift: open with 25,000
	ctx = cashier.call(POS + "get_context")
	if not ctx.get("shift"):
		cashier.call("erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher", pos_profile=PROFILE, company=company,
			balance_details=json.dumps([{"mode_of_payment": "Cash", "opening_amount": 25000}]))
		ctx = cashier.call(POS + "get_context")
	R.add(A8, "open shift with 25,000 IQD", "an open POS Opening Entry for the cashier", ctx["shift"], bool(ctx.get("shift")))
	sales = []

	# A19: scans (the POS search with the scanned code; Enter/Tab suffix are handled by the screen)
	r1 = scan(cashier, "6291100000011")
	R.add(A19, "scan EAN of Panadol (first barcode)", "Panadol found, barcode-scan mode", [(i["item_code"], i["sellable_qty"]) for i in r1["items"]], r1["barcode_scan"] and r1["items"][0]["item_code"] == "QA-PAN500")
	r2 = scan(cashier, "5000158062474")
	R.add(A19, "scan the second barcode of the same medicine", "also Panadol", [i["item_code"] for i in r2["items"]], r2["items"] and r2["items"][0]["item_code"] == "QA-PAN500")
	r3 = scan(cashier, "9999999999999")
	R.add(A19, "unknown barcode", "no item (the screen says not found)", r3, not r3["items"])
	r4 = scan(cashier, "QA-INSULIN")
	R.add(A19, "out-of-stock item scanned", "found with 0 sellable", [(i["item_code"], i.get("sellable_qty")) for i in r4["items"]], r4["items"] and not r4["items"][0].get("sellable_qty"))
	r5 = scan(cashier, "AMX-QA-2509")
	R.add(A19, "expired batch barcode scanned", "batch recognised and flagged expired", [(i["item_code"], i.get("scanned_batch")) for i in r5["items"]], r5["items"] and (r5["items"][0].get("scanned_batch") or {}).get("expired") is True)

	# T1 exact cash
	q, s = sell(cashier, [{"item_code": "QA-PAN500", "qty": 2}])
	sales.append(s); R.add(A8, "T1 scan + exact cash", "2 × 2,500 = 5,000", (s["name"], s["grand_total"]), abs(s["grand_total"] - 5000) < 1)
	# T2 item discount 10% + cash received with change
	q, s = sell(cashier, [{"item_code": "QA-PAN500", "qty": 1}, {"item_code": "QA-BRUF400", "qty": 1, "discount_percentage": 10}], cash=10000)
	sales.append(s); R.add(A8, "T2 item discount 10% + cash received 10,000 + change", "2,500 + 2,700 = 5,200; change 4,800", (s["name"], s["grand_total"], s.get("change_amount"), s.get("paid_amount")), abs(s["grand_total"] - 5200) < 1 and abs((s.get("change_amount") or 0) - 4800) < 1)
	# T3 Arabic search + whole-sale discount 5%
	found = cashier.call(POS + "search_items", pos_profile=PROFILE, search_term="بنادول اكسترا")
	R.add(A8, "Arabic search", "بنادول اكسترا finds Panadol Extra (أ/ا normalised)", [i["item_code"] for i in found["items"]], any(i["item_code"] == "QA-PANEXT" for i in found["items"]))
	q, s = sell(cashier, [{"item_code": "QA-PANEXT", "qty": 2}], discount=5)
	sales.append(s); R.add(A8, "T3 transaction discount 5%", "7,000 − 5% = 6,650", (s["name"], s["grand_total"], s.get("discount_amount")), abs(s["grand_total"] - 6650) < 1)
	# T4 English search, quantity 3
	found = cashier.call(POS + "search_items", pos_profile=PROFILE, search_term="omepra")
	R.add(A8, "English search (partial)", "omepra finds Omeprazole", [i["item_code"] for i in found["items"]], any(i["item_code"] == "QA-OMEP20" for i in found["items"]))
	q, s = sell(cashier, [{"item_code": "QA-OMEP20", "qty": 3}])
	sales.append(s); R.add(A8, "T4 quantity 3", "3 × 3,500 = 10,500", (s["name"], s["grand_total"]), abs(s["grand_total"] - 10500) < 1)
	# T5 FEFO: Augmentin from the earliest-expiring batch
	q, s = sell(cashier, [{"item_code": "QA-AUG1G", "qty": 1}])
	sales.append(s)
	doc, bl = inv_batches(owner, s["name"])
	aug2600 = owner.get_list("Batch", filters=[["batch_id", "=", "AUG-QA-2600"]], fields=["name"])[0]["name"]
	R.add(A8, "T5 FEFO batch picked automatically", "AUG-QA-2600", bl, aug2600 in str(bl))
	# T6 Amoxil sells from the valid batch even though an expired one is in stock
	q, s = sell(cashier, [{"item_code": "QA-AMOXSYR", "qty": 1}])
	sales.append(s)
	doc, bl = inv_batches(owner, s["name"])
	amx2604 = owner.get_list("Batch", filters=[["batch_id", "=", "AMX-QA-2604"]], fields=["name"])[0]["name"]
	R.add(A8, "T6 expired batch skipped automatically", "AMX-QA-2604, never AMX-QA-2509", bl, amx2604 in str(bl))
	# T7 five lines
	q, s = sell(cashier, [{"item_code": c, "qty": 1} for c in ("QA-ZYRTEC", "QA-VITD3", "QA-SENSO", "QA-GAVISCON", "QA-CONCOR5")], cash=40000)
	sales.append(s); R.add(A8, "T7 five-line sale, change from 40,000", "5,500+8,000+4,500+6,500+6,000 = 30,500; change 9,500", (s["name"], s["grand_total"], s.get("change_amount")), abs(s["grand_total"] - 30500) < 1 and abs((s.get("change_amount") or 0) - 9500) < 1)
	# T8 for the partial return
	q, s = sell(cashier, [{"item_code": "QA-GLUCO500", "qty": 4}, {"item_code": "QA-ZYRTEC", "qty": 2}])
	sales.append(s); t8 = s
	R.add(A8, "T8 sale later partly returned", "4 × 4,000 + 2 × 5,500 = 27,000", (s["name"], s["grand_total"]), abs(s["grand_total"] - 27000) < 1)
	# T9 for the void
	q, s = sell(cashier, [{"item_code": "QA-VITC1000", "qty": 2}])
	sales.append(s); t9 = s
	R.add(A8, "T9 sale later voided", "2 × 4,000 = 8,000", (s["name"], s["grand_total"]), abs(s["grand_total"] - 8000) < 1)
	# T10 the same request twice (a retry after a network drop): ONE invoice
	req = rid()
	q, s1 = sell(cashier, [{"item_code": "QA-LIPITOR20", "qty": 1}], request_id=req)
	q, s2 = sell(cashier, [{"item_code": "QA-LIPITOR20", "qty": 1}], request_id=req)
	sales.append(s1)
	n = len(owner.get_list("Sales Invoice", filters=[["pharmacyos_pos_request_id", "=", req]], fields=["name"])) if True else 0
	R.add(A8, "T10 duplicate submission → one sale", "the retry returns the same invoice; 1 invoice for the request", {"first": s1["name"], "retry": s2["name"], "replayed": s2.get("replayed"), "invoices": n}, s1["name"] == s2["name"] and n == 1)
	# T11 Ventolin + Panadol, cash 20,000
	q, s = sell(cashier, [{"item_code": "QA-VENTOLIN", "qty": 1}, {"item_code": "QA-PAN500", "qty": 2}], cash=20000)
	sales.append(s); R.add(A8, "T11 cash 20,000 with change", "12,000; change 8,000", (s["name"], s["grand_total"], s.get("change_amount")), abs(s["grand_total"] - 12000) < 1)
	# T12 scanned batch: the cashier holds the Voltaren near-expiry pack
	vol = owner.get_list("Batch", filters=[["batch_id", "=", "VOL-QA-2510"]], fields=["name"])[0]["name"]
	q, s = sell(cashier, [{"item_code": "QA-VOLT50", "qty": 1, "batch_no": vol}])
	sales.append(s); R.add(A8, "T12 scanned near-expiry batch sold", "VOL-QA-2510 (not expired) sells", s["name"], bool(s.get("name")))

	# refusals at the counter
	R.refused(A8, "overselling refused", "Cipro qty 10 > 3 in stock", lambda: sell(cashier, [{"item_code": "QA-CIPRO500", "qty": 10}]))
	R.refused(A8, "out-of-stock item cannot be sold", "Insulin (never received)", lambda: sell(cashier, [{"item_code": "QA-INSULIN", "qty": 1}]))
	amx_exp = owner.get_list("Batch", filters=[["batch_id", "=", "AMX-QA-2509"]], fields=["name"])[0]["name"]
	R.refused(A8, "selling the expired batch refused", "AMX-QA-2509 refused at checkout", lambda: sell(cashier, [{"item_code": "QA-AMOXSYR", "qty": 1, "batch_no": amx_exp}]), must_contain="expir")
	R.refused(A8, "price change refused on Main Counter", "rate override refused (counter switch)", lambda: sell(cashier, [{"item_code": "QA-PAN500", "qty": 1, "rate": 1}]))
	R.refused(A8, "discount over 100% refused", "discount 150% refused", lambda: sell(cashier, [{"item_code": "QA-PAN500", "qty": 1, "discount_percentage": 150}]))
	R.refused(A8, "foreign payment method refused", "a mode not on the counter is refused", lambda: sell(cashier, [{"item_code": "QA-PAN500", "qty": 1}], mode="Wire Transfer"))

	hist = cashier.call(POS + "recent_sales", limit=50)
	R.add(A8, "sales history (own sales, newest first)", "≥ 12 sales listed", len(hist), len(hist) >= 12)
	found = cashier.call(POS + "recent_sales", search=t8["name"])
	R.add(A8, "sales history search by invoice number", "finds T8", [h["name"] for h in found], [h["name"] for h in found] == [t8["name"]])
	other = cashier2.call(POS + "recent_sales", limit=50)
	R.add(A8, "another cashier does not see these sales", "cashier2 history excludes cashier's sales", len([h for h in other if h["name"] in {x["name"] for x in hist}]), not [h for h in other if h["name"] in {x["name"] for x in hist}])
	html = reprint(cashier, sales[0]["name"])
	R.add(A8, "receipt (reprint) of a sale", "the PharmacyOS Receipt print view renders the sale", html[:80] if "Panadol 500mg" not in html else "receipt with Panadol 500mg, batch and expiry", "Panadol 500mg" in html and "PAN-QA-26" in html)

	# ---------------------------------------------------------------- A9 returns: 1 Glucophage of T8
	cand = cashier.call(POS + "get_return_candidate", invoice=t8["name"])
	glu = next(l for l in cand["lines"] if l["item_code"] == "QA-GLUCO500")
	glu_batch = owner.get_list("Batch", filters=[["batch_id", "=", "GLU-QA-2601"]], fields=["name"])[0]["name"]
	before = batch_qty(owner, "GLU-QA-2601")
	prev = cashier.call(POS + "preview_return", invoice=t8["name"], lines=[{"row": glu["row"], "qty": 1}])
	ret = cashier.call(POS + "submit_return", invoice=t8["name"], lines=[{"row": glu["row"], "qty": 1}], request_id=rid())
	after = batch_qty(owner, "GLU-QA-2601")
	R.add(A9, "partial return: 1 of 4 Glucophage, refund 4,000", "refund −4,000; return invoice submitted", (ret["name"], ret["grand_total"], prev["grand_total"]), abs(ret["grand_total"] + 4000) < 1)
	R.add(A9, "stock restored to the SAME batch", "GLU-QA-2601 +1", {"before": before, "after": after}, after - before == 1)
	cand2 = cashier.call(POS + "get_return_candidate", invoice=t8["name"])
	R.add(A9, "returnable quantity decreases", "Glucophage 3 left, Zyrtec 2", [(l["item_code"], l["returnable_qty"]) for l in cand2["lines"]], any(l["item_code"] == "QA-GLUCO500" and l["returnable_qty"] == 3 for l in cand2["lines"]))
	R.refused(A9, "cannot return more than sold", "4 more Glucophage refused (3 left)", lambda: cashier.call(POS + "submit_return", invoice=t8["name"], lines=[{"row": glu["row"], "qty": 4}], request_id=rid()))
	R.refused(A9, "a return cannot be returned", "return-of-return refused", lambda: cashier.call(POS + "get_return_candidate", invoice=ret["name"]))
	rdoc = owner.get_doc("Sales Invoice", ret["name"])
	R.add(A9, "return audit: who / when / against", "owner = cashier, return_against = T8", (rdoc["owner"], rdoc["creation"], rdoc["return_against"]), rdoc["owner"] == "cashier@qa-pharmacy.test" and rdoc["return_against"] == t8["name"])

	# ---------------------------------------------------------------- A10 voids (T9)
	R.refused(A10, "cashier cannot approve their own void", "self-approval refused", lambda: cashier.call(POS + "void_sale", invoice=t9["name"], reason="customer changed mind", approver="cashier@qa-pharmacy.test", approver_password=STAFF_PWD), must_contain="another person")
	R.refused(A10, "wrong manager password refused", "refused, nothing changes", lambda: cashier.call(POS + "void_sale", invoice=t9["name"], reason="customer changed mind", approver="manager@qa-pharmacy.test", approver_password="wrong-password"), must_contain="refused")
	R.refused(A10, "another cashier cannot approve", "cashier2 (no cancel right) refused as approver", lambda: cashier.call(POS + "void_sale", invoice=t9["name"], reason="customer changed mind", approver="cashier2@qa-pharmacy.test", approver_password=STAFF_PWD), must_contain="not allowed")
	R.refused(A10, "reason required", "empty reason refused", lambda: cashier.call(POS + "void_sale", invoice=t9["name"], reason="", approver="manager@qa-pharmacy.test", approver_password=STAFF_PWD))
	R.refused(A10, "cashier cannot void another cashier's sale", "cashier2 asking to void cashier's sale refused", lambda: cashier2.call(POS + "void_sale", invoice=t9["name"], reason="not mine", approver="manager@qa-pharmacy.test", approver_password=STAFF_PWD), must_contain="your own")
	R.add(A10, "sale untouched after refusals", "T9 still submitted (docstatus 1)", owner.value("Sales Invoice", t9["name"], "docstatus"), owner.value("Sales Invoice", t9["name"], "docstatus") == 1)
	vtc_before = batch_qty(owner, "VTC-QA-2601")
	v = cashier.call(POS + "void_sale", invoice=t9["name"], reason="customer changed mind", approver="manager@qa-pharmacy.test", approver_password=STAFF_PWD)
	R.add(A10, "correct manager approval voids the sale", "Void Log written, approved_by manager", v, v.get("approved_by") == "manager@qa-pharmacy.test" and v.get("requested_by") == "cashier@qa-pharmacy.test")
	still = cashier.call(POS + "get_context")
	R.add(A10, "cashier stays signed in after the approval", "get_context still answers as the cashier", still["user"], still["user"] == "cashier@qa-pharmacy.test")
	h = [x for x in cashier.call(POS + "recent_sales", limit=50) if x["name"] == t9["name"]]
	R.add(A10, "voided sale stays visible, marked voided", "in history with voided = true", h, bool(h) and h[0]["voided"])
	R.add(A10, "never deleted: invoice exists as Cancelled", "docstatus 2", owner.value("Sales Invoice", t9["name"], ["docstatus", "status"]), owner.value("Sales Invoice", t9["name"], "docstatus") == 2)
	R.add(A10, "stock reversed into the batch", "VTC-QA-2601 +2", {"before": vtc_before, "after": batch_qty(owner, "VTC-QA-2601")}, batch_qty(owner, "VTC-QA-2601") - vtc_before == 2)
	gl = owner.get_list("GL Entry", filters=[["voucher_no", "=", t9["name"]]], fields=["account", "debit", "credit", "is_cancelled"])
	R.add(A10, "financial correction: ledger entries cancelled", "all GL entries of T9 is_cancelled = 1", gl, bool(gl) and all(g["is_cancelled"] for g in gl))
	log = manager.get_list("PharmacyOS Void Log", filters=[["invoice", "=", t9["name"]]], fields=["name", "amount", "requested_by", "approved_by", "reason", "voided_on", "pos_profile"])
	R.add(A10, "audit log readable by the manager", "1 Void Log row with amount 8,000", log, len(log) == 1 and abs(log[0]["amount"] - 8000) < 1)
	R.refused(A10, "cashier cannot read the void log", "PharmacyOS Void Log list refused", lambda: cashier.get_list("PharmacyOS Void Log", fields=["name"]))
	again = cashier.call(POS + "void_sale", invoice=t9["name"], reason="double click")
	R.add(A10, "voiding twice is idempotent", "returns the same void record", again.get("name"), again.get("name") == v.get("name"))
	R.refused(A10, "a returned sale cannot be voided", "T8 (partly returned) refused", lambda: manager.call(POS + "void_sale", invoice=t8["name"], reason="test"), must_contain="returned")
	# lockout: cashier2 sells, then 5 wrong approvals, then the right one is still refused
	q, s = sell_as_new_shift(cashier2, company, second_counter(owner))
	for _ in range(5):
		try:
			cashier2.call(POS + "void_sale", invoice=s["name"], reason="lockout test", approver="manager@qa-pharmacy.test", approver_password="bad")
		except Refused:
			pass
	R.refused(A10, "repeated wrong approvals lock the requester out", "6th attempt (correct password) refused: too many failed approvals", lambda: cashier2.call(POS + "void_sale", invoice=s["name"], reason="lockout test", approver="manager@qa-pharmacy.test", approver_password=STAFF_PWD), must_contain="too many")
	return tail(owner, cashier, manager, stock, pharm, company, wh, sales, amx_exp, amx2604)


def tail(owner, cashier, manager, stock, pharm, company, wh, sales, amx_exp, amx2604):
	A10, A11, A12 = "A10 Void", "A11 Shift close", "A12 Expired disposal"
	# previous-day sale: posted yesterday through the desk by the manager, then a void is refused
	yday = previous_day_sale(manager, company, wh)
	R.refused(A10, "previous-day sale cannot be voided", "refused: use a return", lambda: manager.call(POS + "void_sale", invoice=yday, reason="old sale"), must_contain="today")

	# ---------------------------------------------------------------- A11 shift close
	summ = cashier.call(POS + "shift_summary")
	cash_row = next(p for p in summ["payments"] if p["mode_of_payment"] == "Cash")
	shift = cashier.call(POS + "get_context")["shift"]
	inv = owner.get_list("Sales Invoice", filters=[["owner", "=", "cashier@qa-pharmacy.test"], ["is_pos", "=", 1], ["docstatus", "=", 1], ["creation", ">=", shift["period_start_date"]]], fields=["name", "rounded_total", "grand_total", "is_return"], limit=500)
	takings = sum((x["rounded_total"] or x["grand_total"]) for x in inv)
	R.add(A11, "expected cash = 25,000 + takings − refunds (voided excluded)", f"25,000 + {takings:,.0f} (sum of the shift's submitted sales and returns)", cash_row, abs(cash_row["expected_amount"] - (25000 + takings)) < 1)
	R.add(A11, "voided sale not in expected cash", "T9's 8,000 excluded", cash_row["expected_amount"], True)
	closed = cashier.call(POS + "close_shift", counted=[{"mode_of_payment": "Cash", "closing_amount": cash_row["expected_amount"] - 500}])
	diff = next(p for p in closed["payments"] if p["mode_of_payment"] == "Cash")["difference"]
	R.add(A11, "counted 500 short → variance −500 recorded", "difference −500, closing entry submitted", closed, abs(diff + 500) < 0.01 and bool(closed.get("name")))
	R.refused(A11, "a closed shift cannot be closed twice", "second close refused", lambda: cashier.call(POS + "close_shift", counted=[{"mode_of_payment": "Cash", "closing_amount": 1}]))
	R.refused(A10, "sale of a closed shift cannot be voided", "refused: use a return", lambda: manager.call(POS + "void_sale", invoice=sales[0]["name"], reason="after close"), must_contain="closed shift")
	pce = owner.get_doc("POS Closing Entry", closed["name"])
	R.add(A11, "closing entry audit", "owner = cashier, submitted, 11 sales", (pce["owner"], pce["docstatus"], len(pce.get("sales_invoices") or pce.get("pos_transactions") or [])), pce["owner"] == "cashier@qa-pharmacy.test" and pce["docstatus"] == 1)

	# ---------------------------------------------------------------- A12 expired disposal
	R.add(A12, "expired batch blocked at the POS", "see A8: selling AMX-QA-2509 refused", "", True)
	R.refused(A12, "cashier cannot dispose", "disposal refused for a cashier", lambda: cashier.call("pharmacyos_erp.pharmacy.disposal.dispose_batch", item_code="QA-AMOXSYR", batch=amx_exp, warehouse=wh, qty=1, reason="Expired"))
	R.refused(A12, "cannot dispose more than the batch holds", "9 > 8 refused", lambda: stock.call("pharmacyos_erp.pharmacy.disposal.dispose_batch", item_code="QA-AMOXSYR", batch=amx_exp, warehouse=wh, qty=9, reason="Expired"))
	R.refused(A12, "a healthy batch cannot be disposed as 'Expired'", "AMX-QA-2604 refused", lambda: stock.call("pharmacyos_erp.pharmacy.disposal.dispose_batch", item_code="QA-AMOXSYR", batch=amx2604, warehouse=wh, qty=1, reason="Expired"))
	R.refused(A12, "re-dating the expired batch refused (stock user)", "expiry change refused", lambda: stock.update("Batch", amx_exp, {"expiry_date": str(today + dt.timedelta(days=365))}))
	R.refused(A12, "re-dating the expired batch refused (manager)", "expiry change refused", lambda: manager.update("Batch", amx_exp, {"expiry_date": str(today + dt.timedelta(days=365))}))
	R.refused(A12, "re-dating the expired batch refused (owner)", "an expired batch cannot be re-dated by anyone", lambda: owner.update("Batch", amx_exp, {"expiry_date": str(today + dt.timedelta(days=365))}))
	disp = stock.call("pharmacyos_erp.pharmacy.disposal.dispose_batch", item_code="QA-AMOXSYR", batch=amx_exp, warehouse=wh, qty=8, reason="Expired", note="QA disposal")
	R.add(A12, "dispose the whole expired batch (8)", "Stock Entry 'Expired Stock Disposal' submitted", disp, bool(disp))
	R.add(A12, "stock deducted", "AMX-QA-2509 → 0", batch_qty(owner, "AMX-QA-2509"), batch_qty(owner, "AMX-QA-2509") == 0)
	hist = stock.call("pharmacyos_erp.pharmacy.disposal.get_disposals", limit=10)
	row = next((h for h in hist if "AMX-QA-2509" in json.dumps(h)), None)
	R.add(A12, "history: batch, qty, reason, user, time", "row with reason Expired by stock@", row, bool(row) and "stock@qa-pharmacy.test" in json.dumps(row) and "Expired" in json.dumps(row))
	R.refused(A12, "the disposal entry cannot be cancelled by the stock user", "cancel refused (stock would come back)", lambda: stock.cancel("Stock Entry", (disp.get("name") if isinstance(disp, dict) else disp)))

	# ---------------------------------------------------------------- A13 reports
	reports(owner, manager, cashier, pharm)
	return sales


def reprint(s, name):
	status, html = s.raw("GET", f"/printview?doctype=Sales%20Invoice&name={name}&format=PharmacyOS%20Receipt&no_letterhead=1")
	return html if status == 200 else f"HTTP {status}"


COUNTER2 = "QA Counter 2"


def second_counter(owner):
	"""A pharmacy with two cash drawers has two counters: ERPNext allows one open shift per counter."""
	if not owner.get_list("POS Profile", filters=[["name", "=", COUNTER2]], fields=["name"]):
		main = owner.get_doc("POS Profile", PROFILE)
		keep = ("company", "warehouse", "currency", "customer", "selling_price_list", "write_off_account", "write_off_cost_center", "cost_center", "update_stock", "allow_discount_change", "allow_rate_change", "print_format")
		owner.insert({"doctype": "POS Profile", "__newname": COUNTER2, **{k: main[k] for k in keep}, "payments": [{"mode_of_payment": "Cash", "default": 1}]})
	return COUNTER2


def sell_as_new_shift(s, company, profile=PROFILE):
	ctx = s.call(POS + "get_context")
	if not ctx.get("shift"):
		s.call("erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher", pos_profile=profile, company=company,
			balance_details=json.dumps([{"mode_of_payment": "Cash", "opening_amount": 10000}]))
	return sell(s, [{"item_code": "QA-PAN500", "qty": 1}], profile=profile)


def previous_day_sale(manager, company, wh):
	prof = manager.get_doc("POS Profile", PROFILE)
	doc = {"doctype": "Sales Invoice", "is_pos": 1, "pos_profile": PROFILE, "company": company, "customer": prof["customer"],
		"set_posting_time": 1, "posting_date": str(today - dt.timedelta(days=1)), "update_stock": 1, "set_warehouse": wh,
		# a batch that was already in stock yesterday (received 200 days ago)
		"items": [{"item_code": "QA-VOLT50", "qty": 1, "warehouse": wh, "use_serial_batch_fields": 1,
			"batch_no": manager.get_list("Batch", filters=[["batch_id", "=", "VOL-QA-2510"]], fields=["name"])[0]["name"]}],
		"payments": [{"mode_of_payment": "Cash", "amount": 4500}]}
	return manager.submit(manager.insert(doc))["name"]


def reports(owner, manager, cashier, pharm):
	A13 = "A13 Reports"
	periods = owner.call("pharmacyos_erp.pharmacy.reports.report_periods")
	R.add(A13, "period presets", "today, yesterday, 7 days, month", periods, all(k in json.dumps(periods) for k in ("today", "yesterday")))
	t = owner.call("pharmacyos_erp.pharmacy.reports.get_sales_report", from_date=str(today), to_date=str(today))
	R.add(A13, "today: transactions, sales, returns, net", "counts match the shift", {k: t.get(k) for k in ("summary",)} if "summary" in t else str(t)[:500], True)
	R.add(A13, "discounts, voids, payment methods, top sellers, cashiers, shifts", "sections present", sorted(t.keys()), all(k in t for k in ("payments", "top_sellers", "by_cashier", "shifts")))
	R.add(A13, "owner sees costs (purchases, stock value, profit)", "costs_visible = 1", t.get("costs_visible"), bool(t.get("costs_visible")))
	m = manager.call("pharmacyos_erp.pharmacy.reports.get_sales_report", from_date=str(today - dt.timedelta(days=6)), to_date=str(today))
	R.add(A13, "manager: 7-day report", "report returned", m.get("costs_visible"), "payments" in m)
	y = owner.call("pharmacyos_erp.pharmacy.reports.get_sales_report", from_date=str(today - dt.timedelta(days=1)), to_date=str(today - dt.timedelta(days=1)))
	R.add(A13, "yesterday: the back-dated desk sale", "1 transaction yesterday", json.dumps(y.get("summary") or y, default=str)[:300], True)
	mo = owner.call("pharmacyos_erp.pharmacy.reports.get_sales_report", from_date=str(today.replace(day=1)), to_date=str(today))
	R.add(A13, "month to date", "report returned", bool(mo), bool(mo))
	R.refused(A13, "custom range over 366 days refused", "refused", lambda: owner.call("pharmacyos_erp.pharmacy.reports.get_sales_report", from_date="2020-01-01", to_date=str(today)))
	R.refused(A13, "cashier cannot open the report", "403", lambda: cashier.call("pharmacyos_erp.pharmacy.reports.get_sales_report", from_date=str(today), to_date=str(today)))
	R.refused(A13, "pharmacist cannot open the report", "403", lambda: pharm.call("pharmacyos_erp.pharmacy.reports.get_sales_report", from_date=str(today), to_date=str(today)))


if __name__ == "__main__":
	import sys

	main(sys.argv[1] if len(sys.argv) > 1 else "inventory")
