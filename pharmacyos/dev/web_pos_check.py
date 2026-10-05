#!/usr/bin/env python3
"""Web POS acceptance run: the /pos screen's own HTTP path, verified in the ERP's ledgers.

Each cashier is a browser-like session: Frappe login (HTTP-only `sid` cookie), the CSRF token read from
the /pos page, and the same calls the POS screen makes (`pharmacyos_erp.pos.api`, ERPNext's POS
endpoints). The results are then verified server-side with an Administrator API key: the Sales
Invoice, Stock Ledger Entries, GL Entries, batch quantities, Bin, the return ledger.

The run creates its own fictitious medicines, batches, counters and cashier accounts (prefix POSCHK-),
so it never depends on, or disturbs, other data. Development / staging only — never production.

	python3 web_pos_check.py --url http://127.0.0.1:8080 --site <site> --actors actors.json \
		[--cloud-dir /path/to/PharmacyOS/backend --cloud-python /path/to/venv/bin/python]

Checks: login and session, search, barcode, add + quantity, customer, cash sale with change, stock and
batch deduction, FEFO, receipt, partial returns and refund, duplicate submission (sequential and
simultaneous), last-unit concurrency, expired batches, counter permissions, cost confidentiality,
CSRF, Cloud availability (with --cloud-dir: a real PharmacyOS Cloud next to the ERP), logout/login.
"""

import argparse
import concurrent.futures
import json
import os
import re
import secrets
import sys
import threading
import uuid

import datetime
from zoneinfo import ZoneInfo

import requests

SITE_TZ = ZoneInfo(os.environ.get("POS_CHECK_TZ", "Asia/Baghdad"))  # the site's time zone decides "today"


def today() -> str:
	return datetime.datetime.now(SITE_TZ).date().isoformat()


def add_days(date: str, days: int) -> str:
	return (datetime.date.fromisoformat(date) + datetime.timedelta(days=days)).isoformat()

COST_KEYS = ("valuation_rate", "incoming_rate", "last_purchase_rate", "stock_value", "stock_value_difference", "basic_rate")
COST_RATE = 437.123  # receipt (cost) rate of every POSCHK medicine — must never reach a cashier
SALE_RATE = 1000


class Check:
	def __init__(self):
		self.results = []

	def __call__(self, name, ok, detail=""):
		self.results.append((name, bool(ok), detail))
		print(f"{'PASS' if ok else 'FAIL'} {name}{' — ' + str(detail) if detail else ''}")
		return ok


class Admin:
	"""Administrator API key: setup and server-side verification."""

	def __init__(self, url, site, key, secret):
		self.url = url.rstrip("/")
		self.s = requests.Session()
		self.s.headers.update({"Authorization": f"token {key}:{secret}"})
		if site:
			self.s.headers["X-Frappe-Site-Name"] = site

	def call(self, method, **data):
		r = self.s.post(f"{self.url}/api/method/{method}", json=data, timeout=120)
		if r.status_code != 200:
			raise RuntimeError(f"admin {method}: {r.status_code} {r.text[:600]}")
		return r.json().get("message")

	def get_list(self, doctype, filters=None, fields=("name",), limit=500, order_by=None):
		return self.call(
			"frappe.client.get_list", doctype=doctype, filters=filters or {}, fields=list(fields), limit_page_length=limit, order_by=order_by
		)

	def get(self, doctype, name):
		return self.call("frappe.client.get", doctype=doctype, name=name)

	def value(self, doctype, filters, field):
		return (self.call("frappe.client.get_value", doctype=doctype, filters=filters, fieldname=field) or {}).get(field)


class Browser:
	"""What the POS page does in a browser: cookie session + CSRF token from /pos."""

	transcript_lock = threading.Lock()
	transcript = []  # every response body a cashier received (cost-confidentiality scan)

	def __init__(self, url, site, user=None, password=None):
		self.url = url.rstrip("/")
		self.s = requests.Session()
		if site:
			self.s.headers["X-Frappe-Site-Name"] = site
		self.csrf = None
		if user:
			self.login(user, password)

	def login(self, user, password):
		r = self.s.post(f"{self.url}/api/method/login", json={"usr": user, "pwd": password}, timeout=60)
		r.raise_for_status()
		self.open_pos()

	def open_pos(self):
		page = self.s.get(f"{self.url}/pos", timeout=60, allow_redirects=False)
		self.page_status = page.status_code
		m = re.search(r'frappe\.csrf_token = "([0-9a-f]+)"', page.text)
		self.csrf = m.group(1) if m else None
		return page

	def call(self, method, post=False, csrf=True, **args):
		headers = {"Accept": "application/json"}
		if post:
			if csrf and self.csrf:
				headers["X-Frappe-CSRF-Token"] = self.csrf
			r = self.s.post(f"{self.url}/api/method/{method}", json=args, headers=headers, timeout=120)
		else:
			params = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in args.items() if v is not None}
			r = self.s.get(f"{self.url}/api/method/{method}", params=params, headers=headers, timeout=120)
		with Browser.transcript_lock:
			Browser.transcript.append(r.text)
		try:
			body = r.json()
		except ValueError:
			body = {}
		return r.status_code, body.get("message"), body

	def ok(self, method, post=False, **args):
		status, message, body = self.call(method, post=post, **args)
		if status != 200:
			raise RuntimeError(f"{method}: {status} {json.dumps(body)[:600]}")
		return message


def error_text(body) -> str:
	parts = []
	for raw in json.loads(body.get("_server_messages") or "[]"):
		try:
			parts.append(json.loads(raw).get("message", ""))
		except ValueError:
			parts.append(raw)
	return re.sub(r"<[^>]+>", "", " ".join(parts) or str(body.get("exception", "")))[:300]


# ---------------------------------------------------------------- setup (administrator)


def make_medicine(admin, tag, barcode=True):
	code = f"POSCHK-{tag}-{uuid.uuid4().hex[:5].upper()}"
	admin.call(
		"pharmacyos_erp.pharmacy.medicine.create_medicine",
		item_code=code,
		item_name=f"POS Check {tag} {code[-5:]}",
		pharma_name_ar=f"دواء فحص {tag} {code[-5:]}",
		item_group="Products",
		stock_uom="Nos",
		standard_rate=SALE_RATE,
		barcode=("2" + str(uuid.uuid4().int)[:12]) if barcode else None,
	)
	return code


def make_batch(admin, item, expiry):
	return admin.call(
		"frappe.client.insert",
		doc={"doctype": "Batch", "batch_id": f"B-{uuid.uuid4().hex[:8].upper()}", "item": item, "expiry_date": expiry},
	)["name"]


def receive(admin, company, warehouse, item, batch, qty, posting_date=None):
	doc = {
		"doctype": "Stock Entry",
		"stock_entry_type": "Material Receipt",
		"company": company,
		"docstatus": 1,
		"items": [
			{"item_code": item, "qty": qty, "t_warehouse": warehouse, "basic_rate": COST_RATE, "batch_no": batch, "use_serial_batch_fields": 1}
		],
	}
	if posting_date:
		doc.update({"set_posting_time": 1, "posting_date": posting_date})
	return admin.call("frappe.client.insert", doc=doc)["name"]


def bin_qty(admin, item, warehouse):
	return float(admin.value("Bin", {"item_code": item, "warehouse": warehouse}, "actual_qty") or 0)


def batch_qty(admin, batch, warehouse):
	"""ERPNext's own batch balance (expired batches included: for_stock_levels)."""
	return float(admin.call("erpnext.stock.doctype.batch.batch.get_batch_qty", batch_no=batch, warehouse=warehouse, for_stock_levels=True) or 0)


def make_cashier(admin, password):
	email = f"poschk-{uuid.uuid4().hex[:6]}@pos-test.invalid"
	admin.call(
		"frappe.client.insert",
		doc={
			"doctype": "User",
			"email": email,
			"first_name": "POS Check Cashier",
			"send_welcome_email": 0,
			"language": "ar",
			"role_profile_name": "Cashier",
			"new_password": password,
		},
	)
	return email


def make_counter(admin, company, warehouse, users, discounts=0):
	name = f"POSCHK Counter {uuid.uuid4().hex[:5].upper()}"
	walk_in = admin.value("Customer", {"customer_name": "زبون نقدي"}, "name") or admin.get_list("Customer", limit=1)[0]["name"]
	admin.call(
		"frappe.client.insert",
		doc={
			"doctype": "POS Profile",
			"__newname": name,
			"company": company,
			"warehouse": warehouse,
			"customer": walk_in,
			"currency": admin.value("Company", company, "default_currency"),
			"selling_price_list": "Standard Selling",
			"write_off_account": admin.value("Company", company, "write_off_account"),
			"write_off_cost_center": admin.value("Company", company, "cost_center"),
			"cost_center": admin.value("Company", company, "cost_center"),
			"update_stock": 1,
			"allow_discount_change": discounts,
			"allow_rate_change": 0,
			"print_format": "PharmacyOS Receipt",
			"payments": [{"mode_of_payment": "Cash", "default": 1}],
			"applicable_for_users": [{"user": u} for u in users],
		},
	)
	return name


# ---------------------------------------------------------------- the run


def main():
	p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
	p.add_argument("--url", default="http://127.0.0.1:8080", help="the POS host (nginx), as a browser reaches it")
	p.add_argument("--site", default="", help="Frappe site (sent as X-Frappe-Site-Name; empty behind a POS host)")
	p.add_argument("--actors", required=True, help="JSON with an Administrator API key/secret")
	p.add_argument("--warehouse", help="warehouse of the test counters (default: the first branch's)")
	p.add_argument("--rounds", type=int, default=5, help="last-unit / duplicate concurrency rounds")
	p.add_argument("--bench", default="/home/frappe/frappe-bench")
	p.add_argument("--bench-cmd", default="bench", help="bench executable (run as the frappe user)")
	p.add_argument("--bench-site", help="site name for `bench execute` (default: --site)")
	p.add_argument("--cloud-dir")
	p.add_argument("--cloud-python")
	args = p.parse_args()

	creds = json.load(open(args.actors))["Administrator"]
	admin = Admin(args.url, args.site, creds["key"], creds["secret"])
	check = Check()
	company = admin.call("frappe.client.get_value", doctype="Global Defaults", filters="Global Defaults", fieldname="default_company")[
		"default_company"
	]
	cloud = None
	if args.cloud_dir:
		cloud = start_cloud(admin, args)  # sells from the Cloud's branch, so the website sees it
	warehouse = args.warehouse or admin.get_list("Branch", fields=("pharmacyos_warehouse",), limit=1)[0]["pharmacyos_warehouse"]
	print(f"company {company}, warehouse {warehouse}")

	try:
		run(admin, check, company, warehouse, args, cloud)
	finally:
		if cloud:
			cloud.stop()
			settings = admin.get("PharmacyOS Settings", "PharmacyOS Settings")
			settings.update(cloud.restore)  # the site goes back to its own Cloud settings
			admin.call("frappe.client.save", doc=settings)
	failures = [r for r in check.results if not r[1]]
	print(json.dumps({"checks": len(check.results), "failures": len(failures)}))
	sys.exit(1 if failures else 0)


def run(admin, check, company, warehouse, args, cloud):
	pw1, pw2 = "Chk-" + secrets.token_urlsafe(10), "Chk-" + secrets.token_urlsafe(10)
	cashier1, cashier2 = make_cashier(admin, pw1), make_cashier(admin, pw2)
	counter_a = make_counter(admin, company, warehouse, [cashier1])
	counter_b = make_counter(admin, company, warehouse, [cashier2], discounts=1)

	# FEFO medicine: the later-expiring batch is received FIRST, so FIFO and FEFO disagree
	fefo = make_medicine(admin, "FEFO")
	late = make_batch(admin, fefo, add_days(today(), 400))
	early = make_batch(admin, fefo, add_days(today(), 40))
	receive(admin, company, warehouse, fefo, late, 10)
	receive(admin, company, warehouse, fefo, early, 10)
	fefo_barcode = admin.get("Item", fefo)["barcodes"][0]["barcode"]

	# ------------------------------------------------------------ 1. login / session / CSRF
	guest = Browser(args.url, args.site)
	page = guest.s.get(f"{args.url}/pos", allow_redirects=False, timeout=30)
	check("guest /pos → sign-in", page.status_code in (301, 302) and "/login" in page.headers.get("Location", ""), page.headers.get("Location"))
	status, _m, _b = guest.call("pharmacyos_erp.pos.api.get_context")
	check("guest API refused", status in (401, 403), status)
	status, _m, _b = guest.call("pharmacyos_erp.pos.api.checkout", post=True, pos_profile=counter_a, items=[], payments=[], request_id="x" * 20)
	check("guest checkout refused", status in (401, 403), status)

	c1 = Browser(args.url, args.site, cashier1, pw1)
	c2 = Browser(args.url, args.site, cashier2, pw2)
	sid = c1.s.cookies.get("sid")
	check("login → /pos page with CSRF token", c1.page_status == 200 and bool(c1.csrf))
	login = requests.post(f"{args.url}/api/method/login", json={"usr": cashier1, "pwd": pw1}, timeout=30, headers={"X-Frappe-Site-Name": args.site} if args.site else {})
	cookie = login.headers.get("Set-Cookie", "")
	check("session cookie HttpOnly + SameSite=Lax", "HttpOnly" in cookie and "SameSite=Lax" in cookie, re.sub(r"sid=[0-9a-f]+", "sid=…", cookie.split(",")[0]))
	check("session cookie present", bool(sid))
	status, _m, body = c1.call("pharmacyos_erp.pos.api.quote", post=True, csrf=False, pos_profile=counter_a, items=[{"item_code": fefo, "qty": 1}])
	check("POST without CSRF token refused", status in (400, 403) and "CSRF" in json.dumps(body), status)

	ctx = c1.ok("pharmacyos_erp.pos.api.get_context")
	check("context: can sell, no shift yet, own counter only", ctx["can_sell"] and not ctx["shift"] and [p["name"] for p in ctx["profiles"]] == [counter_a], [p["name"] for p in ctx["profiles"]])
	for browser, counter in ((c1, counter_a), (c2, counter_b)):
		browser.ok(
			"erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher",
			post=True,
			pos_profile=counter,
			company=company,
			balance_details=[{"mode_of_payment": "Cash", "opening_amount": 0}],
		)
	ctx = c1.ok("pharmacyos_erp.pos.api.get_context")
	check("shift opened by the cashier", ctx["shift"] and ctx["profile"]["name"] == counter_a, ctx["shift"] and ctx["shift"]["name"])

	# ------------------------------------------------------------ 2/3. search & barcode
	found = c1.ok("pharmacyos_erp.pos.api.search_items", pos_profile=counter_a, search_term="دواء فحص FEFO")
	check("search by Arabic name", any(i["item_code"] == fefo for i in found["items"]), len(found["items"]))
	scan = c1.ok("pharmacyos_erp.pos.api.search_items", pos_profile=counter_a, search_term=fefo_barcode)
	item = (scan["items"] or [{}])[0]
	check("barcode scan → exactly this medicine", scan["barcode_scan"] and len(scan["items"]) == 1 and item.get("item_code") == fefo)
	check("scan shows FEFO batch and sellable stock", item.get("sellable_qty") == 20 and item.get("next_batch", {}).get("expiry_date") == add_days(today(), 40), item.get("next_batch"))
	check("scan result has no cost keys", not any(k in json.dumps(scan) for k in COST_KEYS))

	# ------------------------------------------------------------ 4/5/6. cart, quantity, customer
	quote = c1.ok("pharmacyos_erp.pos.api.quote", post=True, pos_profile=counter_a, items=[{"item_code": fefo, "qty": 3}])
	check("quote = ERPNext price × qty", quote["grand_total"] == 3 * SALE_RATE and quote["items"][0]["rate"] == SALE_RATE, quote["grand_total"])
	customer = c1.ok("frappe.client.insert", post=True, doc={"doctype": "Customer", "customer_name": f"زبون فحص {uuid.uuid4().hex[:4]}", "customer_type": "Individual", "mobile_no": "07700000001"})
	found_c = c1.ok("frappe.desk.search.search_link", doctype="Customer", txt=customer["customer_name"])
	check("cashier creates and finds a customer", any(r["value"] == customer["name"] for r in found_c))

	# ------------------------------------------------------------ 7-11. cash sale and its ledgers
	bin_before = bin_qty(admin, fefo, warehouse)
	early_before, late_before = batch_qty(admin, early, warehouse), batch_qty(admin, late, warehouse)
	rid = str(uuid.uuid4())
	sale = c1.ok(
		"pharmacyos_erp.pos.api.checkout",
		post=True,
		pos_profile=counter_a,
		items=[{"item_code": fefo, "qty": 3}],
		payments=[{"mode_of_payment": "Cash", "amount": 5000}],
		request_id=rid,
		customer=customer["name"],
	)
	check("cash sale submitted", sale["docstatus"] == 1 and sale["status"] == "Paid" and not sale["replayed"], sale["name"])
	check("change computed by ERPNext", sale["change_amount"] == 2000 and sale["paid_amount"] == 5000, sale["change_amount"])
	inv = admin.get("Sales Invoice", sale["name"])
	check("invoice: POS, stock updated, own counter and cashier", inv["is_pos"] and inv["update_stock"] and inv["pos_profile"] == counter_a and inv["owner"] == cashier1)
	sle = admin.get_list("Stock Ledger Entry", {"voucher_no": sale["name"], "is_cancelled": 0}, ("actual_qty", "warehouse", "item_code"))
	check("SLE: −3 from the counter's warehouse", sum(r["actual_qty"] for r in sle) == -3 and {r["warehouse"] for r in sle} == {warehouse}, sle)
	check("Bin: −3", bin_qty(admin, fefo, warehouse) == bin_before - 3)
	check("FEFO: earliest expiry batch used", batch_qty(admin, early, warehouse) == early_before - 3 and batch_qty(admin, late, warehouse) == late_before, (batch_qty(admin, early, warehouse), batch_qty(admin, late, warehouse)))
	gl = admin.get_list("GL Entry", {"voucher_no": sale["name"], "is_cancelled": 0}, ("account", "debit", "credit"))
	debit, credit = sum(g["debit"] for g in gl), sum(g["credit"] for g in gl)
	income = sum(g["credit"] for g in gl if g["account"] == inv["items"][0]["income_account"])
	check("GL balanced, income credited 3000", abs(debit - credit) < 0.01 and income == 3 * SALE_RATE, (debit, credit, income))
	receipt = c1.s.get(
		f"{args.url}/printview", params={"doctype": "Sales Invoice", "name": sale["name"], "format": "PharmacyOS Receipt", "no_letterhead": 1, "_lang": "ar"}, timeout=60
	)
	Browser.transcript.append(receipt.text)
	text = re.sub(r"<[^>]+>", " ", receipt.text)
	check("receipt: invoice, cashier, Arabic, total, payment", receipt.status_code == 200 and sale["name"] in text and "POS Check Cashier" in text and "أمين الصندوق" in text and "3,000" in text, receipt.status_code)
	check("receipt: no cost figures", str(COST_RATE) not in receipt.text and "437" not in text)

	# ------------------------------------------------------------ 14. duplicate submission
	again = c1.ok("pharmacyos_erp.pos.api.checkout", post=True, pos_profile=counter_a, items=[{"item_code": fefo, "qty": 3}], payments=[{"mode_of_payment": "Cash", "amount": 5000}], request_id=rid, customer=customer["name"])
	check("same request again → same invoice, nothing sold twice", again["name"] == sale["name"] and again["replayed"] and bin_qty(admin, fefo, warehouse) == bin_before - 3)
	other = c2.call("pharmacyos_erp.pos.api.checkout", post=True, pos_profile=counter_b, items=[{"item_code": fefo, "qty": 1}], payments=[{"mode_of_payment": "Cash", "amount": 1000}], request_id=rid)
	check("another user's request ID is refused", other[0] == 403, other[0])
	for rnd in range(args.rounds):
		rid2 = str(uuid.uuid4())
		before = bin_qty(admin, fefo, warehouse)
		with concurrent.futures.ThreadPoolExecutor(6) as pool:
			# six browser tabs / retries of one checkout, released together
			outs = list(pool.map(lambda _i: c1.call("pharmacyos_erp.pos.api.checkout", post=True, pos_profile=counter_a, items=[{"item_code": fefo, "qty": 1}], payments=[{"mode_of_payment": "Cash", "amount": 1000}], request_id=rid2), range(6)))
		names = {o[1]["name"] for o in outs if o[0] == 200}
		stored = admin.get_list("Sales Invoice", {"pharmacyos_pos_request_id": rid2}, ("name",))
		dup_sale = next(iter(names), None)
		check(f"simultaneous duplicate checkout round {rnd + 1}: one sale", len(names) == 1 and len(stored) == 1 and bin_qty(admin, fefo, warehouse) == before - 1, ([o[0] for o in outs], names))

	# ------------------------------------------------------------ 12/13. returns and refunds
	cand = c1.ok("pharmacyos_erp.pos.api.get_return_candidate", invoice=sale["name"])
	row = cand["lines"][0]
	check("return candidate: 3 returnable", row["returnable_qty"] == 3, row)
	prev = c1.ok("pharmacyos_erp.pos.api.preview_return", post=True, invoice=sale["name"], lines=[{"row": row["row"], "qty": 1}])
	check("refund preview by ERPNext: −1000", prev["grand_total"] == -SALE_RATE, prev["grand_total"])
	before_ret = batch_qty(admin, early, warehouse)
	ret = c1.ok("pharmacyos_erp.pos.api.submit_return", post=True, invoice=sale["name"], lines=[{"row": row["row"], "qty": 1}], request_id=str(uuid.uuid4()))
	rdoc = admin.get("Sales Invoice", ret["name"])
	check("return submitted against the sale", rdoc["docstatus"] == 1 and rdoc["is_return"] and rdoc["return_against"] == sale["name"] and rdoc["grand_total"] == -SALE_RATE)
	check("refund paid out in cash", sum(p["amount"] for p in rdoc["payments"]) == -SALE_RATE, rdoc["payments"])
	check("returned unit back into its batch", batch_qty(admin, early, warehouse) == before_ret + 1)
	rgl = admin.get_list("GL Entry", {"voucher_no": ret["name"], "is_cancelled": 0}, ("account", "debit", "credit"))
	cash_credit = sum(g["credit"] - g["debit"] for g in rgl if g["account"] == rdoc["payments"][0]["account"])
	check("GL: cash credited 1000 for the refund", abs(cash_credit - SALE_RATE) < 0.01, cash_credit)
	over = c1.call("pharmacyos_erp.pos.api.submit_return", post=True, invoice=sale["name"], lines=[{"row": row["row"], "qty": 3}], request_id=str(uuid.uuid4()))
	check("returning more than left is refused", over[0] == 417, (over[0], error_text(over[2])))
	chain = c1.call("pharmacyos_erp.pos.api.get_return_candidate", invoice=ret["name"])
	check("return against a return is refused", chain[0] == 417, chain[0])
	rest = c1.ok("pharmacyos_erp.pos.api.submit_return", post=True, invoice=sale["name"], lines=[{"row": row["row"], "qty": 2}], request_id=str(uuid.uuid4()))
	cand = c1.ok("pharmacyos_erp.pos.api.get_return_candidate", invoice=sale["name"])
	check("after full return nothing is returnable", rest["grand_total"] == -2 * SALE_RATE and cand["lines"][0]["returnable_qty"] == 0)
	ledger = json.loads(admin.value("Sales Invoice", sale["name"], "pharma_return_ledger") or "{}")
	check("PharmacyOS return ledger on the sale: 3 returned", sum(e["items"][fefo] for e in ledger["returns"].values()) == 3)

	# ------------------------------------------------------------ 16. expired batches
	exp_item = make_medicine(admin, "EXP")
	expired = make_batch(admin, exp_item, add_days(today(), -3))
	try:
		receive(admin, company, warehouse, exp_item, expired, 5, posting_date=add_days(today(), -20))
		received = True
	except RuntimeError as e:
		received = False
		print("  (expired stock could not be received for the test:", str(e)[:160], ")")
	if received:
		s = c1.ok("pharmacyos_erp.pos.api.search_items", pos_profile=counter_a, search_term=exp_item)
		it = s["items"][0]
		check("expired-only stock: sellable 0 shown", it["sellable_qty"] == 0 and it["actual_qty"] == 5, (it["sellable_qty"], it["actual_qty"]))
		for label, line in (("auto batch", {"item_code": exp_item, "qty": 1}), ("expired batch chosen", {"item_code": exp_item, "qty": 1, "batch_no": expired})):
			r = c1.call("pharmacyos_erp.pos.api.checkout", post=True, pos_profile=counter_a, items=[line], payments=[{"mode_of_payment": "Cash", "amount": SALE_RATE}], request_id=str(uuid.uuid4()))
			check(f"expired stock never sold ({label})", r[0] == 417 and bin_qty(admin, exp_item, warehouse) == 5, (r[0], error_text(r[2])))

	# ------------------------------------------------------------ 15. last-unit concurrency
	last = make_medicine(admin, "LAST")
	for rnd in range(args.rounds):
		receive(admin, company, warehouse, last, make_batch(admin, last, add_days(today(), 200)), 1)
		barrier = threading.Barrier(2)

		def buy(browser, counter):
			barrier.wait()
			return browser.call("pharmacyos_erp.pos.api.checkout", post=True, pos_profile=counter, items=[{"item_code": last, "qty": 1}], payments=[{"mode_of_payment": "Cash", "amount": SALE_RATE}], request_id=str(uuid.uuid4()))

		with concurrent.futures.ThreadPoolExecutor(2) as pool:
			outs = list(pool.map(lambda bc: buy(*bc), ((c1, counter_a), (c2, counter_b))))
		statuses = sorted(o[0] for o in outs)
		check(f"last unit round {rnd + 1}: one sale, one controlled refusal, stock 0", statuses == [200, 417] and bin_qty(admin, last, warehouse) == 0, (statuses, [error_text(o[2]) for o in outs if o[0] != 200]))

	# ------------------------------------------------------------ 17. counter permissions
	r = c1.call("pharmacyos_erp.pos.api.quote", post=True, pos_profile=counter_a, items=[{"item_code": fefo, "qty": 1, "discount_percentage": 10}])
	check("no discount on a counter without discount rights", r[0] == 403, r[0])
	r = c1.call("pharmacyos_erp.pos.api.checkout", post=True, pos_profile=counter_a, items=[{"item_code": fefo, "qty": 1, "rate": 1}], payments=[{"mode_of_payment": "Cash", "amount": 1}], request_id=str(uuid.uuid4()))
	check("no price change on the counter", r[0] == 403, r[0])
	r = c1.call("pharmacyos_erp.pos.api.search_items", pos_profile=counter_b)
	check("another cashier's counter refused", r[0] == 403, r[0])
	q = c2.ok("pharmacyos_erp.pos.api.quote", post=True, pos_profile=counter_b, items=[{"item_code": fefo, "qty": 1, "discount_percentage": 10}])
	check("discount on a counter that allows it (ERPNext calculates)", q["grand_total"] == 900, q["grand_total"])
	r = c1.call("frappe.client.cancel", post=True, doctype="Sales Invoice", name=dup_sale)
	check("cashier cannot cancel a sale", r[0] == 403 and admin.value("Sales Invoice", dup_sale, "docstatus") == 1, r[0])
	r = c1.call("frappe.client.cancel", post=True, doctype="Sales Invoice", name=sale["name"])
	# round-4 guard: a sale with submitted returns is refused before anything else (any role)
	check("returned sale cannot be cancelled", r[0] in (403, 417) and admin.value("Sales Invoice", sale["name"], "docstatus") == 1, (r[0], r[2].get("exc_type")))
	r = c1.call("frappe.client.get_list", doctype="Stock Ledger Entry", fields=["name"])
	check("cashier cannot read the stock ledger", r[0] == 403, r[0])
	r = c1.call("frappe.client.get_list", doctype="GL Entry", fields=["name"])
	check("cashier cannot read the general ledger", r[0] == 403, r[0])

	# ------------------------------------------------------------ 19. Cloud availability
	if cloud:
		run_cloud(admin, check, cloud, c1, counter_a, company, warehouse, args)

	# ------------------------------------------------------------ 18. cost confidentiality
	leaks = [k for k in COST_KEYS if any(f'"{k}"' in t for t in Browser.transcript)]
	value_leak = any(str(COST_RATE) in t for t in Browser.transcript)
	check(f"no cost data in {len(Browser.transcript)} cashier responses", not leaks and not value_leak, leaks)

	# ------------------------------------------------------------ 20. logout / login
	c1.call("logout", post=True)
	r = c1.call("pharmacyos_erp.pos.api.get_context")
	check("after sign-out the API refuses", r[0] in (401, 403), r[0])
	c1.login(cashier1, pw1)
	ctx2 = c1.ok("pharmacyos_erp.pos.api.get_context")
	recent = c1.ok("pharmacyos_erp.pos.api.recent_sales", limit=50)
	check("sign in again: same shift, sales listed", ctx2["shift"]["name"] == ctx["shift"]["name"] and any(r["name"] == sale["name"] for r in recent))


# ---------------------------------------------------------------- Cloud (optional)


def start_cloud(admin, args):
	sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
	from live_cloud_check import Cloud  # the Cloud harness of the live Cloud ↔ ERP check

	secret = secrets.token_urlsafe(48)
	cloud = Cloud(args.cloud_dir, args.cloud_python, secret)
	branch = admin.get_list("Branch", filters={"pharmacyos_storefront_code": ["is", "set"]}, fields=("name", "pharmacyos_warehouse", "pharmacyos_storefront_code"), limit=1)[0]
	cloud.env["ERP_ORDER_BRANCH_CODE"] = branch["pharmacyos_storefront_code"]
	args.warehouse = args.warehouse or branch["pharmacyos_warehouse"]
	settings = admin.get("PharmacyOS Settings", "PharmacyOS Settings")
	cloud.restore = {k: settings.get(k) for k in ("enable_outbound_events", "cloud_base_url", "outbound_endpoint", "online_order_branch")}
	settings.update({"enable_outbound_events": 1, "cloud_base_url": cloud.base, "outbound_endpoint": None, "outbound_secret": secret, "online_order_branch": branch["name"]})
	admin.call("frappe.client.save", doc=settings)
	cloud.start()
	return cloud


def run_job(args, path):
	import subprocess

	out = subprocess.run(["sudo", "-u", "frappe", "-H", "bash", "-c", f"cd {args.bench} && {args.bench_cmd} --site {args.bench_site or args.site} execute {path}"], capture_output=True, text=True, timeout=600)
	if out.returncode:
		raise RuntimeError(out.stderr[-600:])


def run_cloud(admin, check, cloud, c1, counter, company, warehouse, args):
	item = make_medicine(admin, "CLOUD")
	receive(admin, company, warehouse, item, make_batch(admin, item, add_days(today(), 300)), 6)
	admin.call("frappe.client.set_value", doctype="Item", name=item, fieldname="pharmacyos_publish", value=1)
	run_job(args, "pharmacyos_erp.integration.outbox.process_outbox")
	before = cloud.product(item)
	check("Cloud: medicine published with ERP stock", before and before["sellable"] == 6, before)
	sale = c1.ok("pharmacyos_erp.pos.api.checkout", post=True, pos_profile=counter, items=[{"item_code": item, "qty": 2}], payments=[{"mode_of_payment": "Cash", "amount": 2 * SALE_RATE}], request_id=str(uuid.uuid4()))
	run_job(args, "pharmacyos_erp.integration.outbox.process_outbox")
	after = cloud.product(item)
	check("Cloud: POS sale reduces website availability (6 → 4)", after and after["sellable"] == 4, after)
	row = c1.ok("pharmacyos_erp.pos.api.get_return_candidate", invoice=sale["name"])["lines"][0]
	c1.ok("pharmacyos_erp.pos.api.submit_return", post=True, invoice=sale["name"], lines=[{"row": row["row"], "qty": 1}], request_id=str(uuid.uuid4()))
	run_job(args, "pharmacyos_erp.integration.outbox.process_outbox")
	back = cloud.product(item)
	check("Cloud: POS return restores availability (4 → 5)", back and back["sellable"] == 5, back)


if __name__ == "__main__":
	main()
