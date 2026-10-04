#!/usr/bin/env python3
"""Real-HTTP concurrency acceptance check for PharmacyOS ERP (multi-worker gunicorn).

The in-suite regression tests use Frappe's in-process test client with threads. This tool hits a real
server (several gunicorn worker processes, each with its own database connections) with requests that
are released at the same instant, then checks the invariants from the stock ledger and the GL:

* returns:   cumulative submitted return quantity per item never exceeds the quantity sold; the loser
             of a race gets a controlled 417 (StockOverReturnError), never a 5xx or a deadlock;
* last unit: two counter sales of the last unit → exactly one succeeds, no 5xx;
* checkout:  the same website order sent several times at once → one Sales Order, every caller gets it;
* unrelated_cancel (round 4): a sale (and a draft) racing the cancellation of another document — a
             return or sale of a different medicine, or the medicine's own earlier return — never ends
             in a database deadlock (HTTP 508): every request gets 200 or a controlled business error.

After every round it verifies: batch quantity == received − sold + accepted returns (no phantom
stock), every submitted voucher's GL is balanced, and no response was a 5xx.

Usage (from anywhere; needs `requests`):

	python3 concurrency_check.py --url http://127.0.0.1:8000 --site <site> \
		--key <api key> --secret <api secret> [--integration-key K --integration-secret S] \
		[--rounds 8] [--only returns,last_unit,checkout]

The API user must be able to create medicines, receive stock and sell (Pharmacy Owner/Manager).
Creates its own uniquely named medicines; never point it at a production site.
"""

import argparse
import json
import sys
import threading
import time
import uuid
from collections import Counter

import requests


class Api:
	def __init__(self, url, site, key, secret):
		self.url = url.rstrip("/")
		self.headers = {"Authorization": f"token {key}:{secret}", "Host": site, "Accept": "application/json"}

	def session(self):
		s = requests.Session()
		s.headers.update(self.headers)
		return s

	def call(self, method, session=None, http="post", **data):
		s = session or self.session()
		if http == "get":
			r = s.get(f"{self.url}/api/method/{method}", params=data, timeout=120)
		else:
			r = s.post(f"{self.url}/api/method/{method}", json=data, timeout=120)
		return r

	def ok(self, method, **data):
		r = self.call(method, **data)
		if r.status_code != 200:
			try:
				detail = json.loads(r.json().get("exc", '[""]'))[0][-3500:]
			except Exception:
				detail = r.text[:600]
			raise RuntimeError(f"{method}: {r.status_code} {detail}")
		return r.json().get("message")

	def get_list(self, doctype, **kwargs):
		return self.ok("frappe.client.get_list", doctype=doctype, limit_page_length=0, **kwargs)


def simultaneous(fns):
	"""Run callables at the same instant (threads released by one barrier); returns their results."""
	barrier = threading.Barrier(len(fns))
	results = [None] * len(fns)

	def run(i, fn):
		barrier.wait()
		try:
			results[i] = fn()
		except Exception as e:  # network errors are results too
			results[i] = ("exception", repr(e))

	threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(fns)]
	for t in threads:
		t.start()
	for t in threads:
		t.join()
	return results


class Shop:
	"""Context of the site: company, warehouse, customer, cash account."""

	def __init__(self, api):
		self.api = api
		self.company = api.get_list("Company", fields=["name"])[0]["name"]
		branch = api.get_list(
			"Branch",
			fields=["name", "pharmacyos_warehouse", "pharmacyos_storefront_code"],
			filters={"pharmacyos_warehouse": ["is", "set"]},
		)[0]
		self.warehouse = branch["pharmacyos_warehouse"]
		self.branch_code = branch["pharmacyos_storefront_code"]
		self.currency = api.ok(
			"frappe.client.get_value", doctype="Company", filters=self.company, fieldname="default_currency"
		)["default_currency"]
		self.customer = self._customer()
		self._cash_account()

	def _customer(self):
		name = "Concurrency Check Customer"
		found = self.api.get_list("Customer", filters={"customer_name": name})
		if found:
			return found[0]["name"]
		group = self.api.get_list("Customer Group", filters={"is_group": 0})[0]["name"]
		return self.api.ok(
			"frappe.client.insert",
			doc={
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Individual",
				"customer_group": group,
			},
		)["name"]

	def _cash_account(self):
		mop = self.api.ok("frappe.client.get", doctype="Mode of Payment", name="Cash")
		own = [a for a in mop.get("accounts", []) if a.get("company") == self.company]
		if own:
			self.cash = own[0]["default_account"]
			return
		self.cash = self.api.get_list(
			"Account", filters={"company": self.company, "account_type": "Cash", "is_group": 0}
		)[0]["name"]
		mop["accounts"].append({"company": self.company, "default_account": self.cash})
		self.api.ok("frappe.client.save", doc=mop)

	def medicine(self, stock):
		code = "CC-" + uuid.uuid4().hex[:8].upper()
		self.api.ok(
			"pharmacyos_erp.pharmacy.medicine.create_medicine",
			item_code=code,
			item_name=f"Concurrency {code}",
			item_group="Products",
			stock_uom="Nos",
			standard_rate=1000,
		)
		batch = self.api.ok(
			"pharmacyos_erp.pharmacy.receiving.create_receiving_batch",
			item_code=code,
			batch_id=f"B-{code}",
			expiry_date="2099-12-31",
		)["name"]
		if stock:
			self.api.ok(
				"frappe.client.insert",
				doc={
					"doctype": "Stock Entry",
					"stock_entry_type": "Material Receipt",
					"company": self.company,
					"docstatus": 1,
					"items": [
						{
							"item_code": code,
							"qty": stock,
							"t_warehouse": self.warehouse,
							"basic_rate": 500,
							"batch_no": batch,
							"use_serial_batch_fields": 1,
						}
					],
				},
			)
		return code, batch

	def sale_doc(self, code, batch, qty, return_against=None):
		sign = -1 if return_against else 1
		return {
			"doctype": "Sales Invoice",
			"company": self.company,
			"customer": self.customer,
			"currency": self.currency,
			"is_pos": 1,
			"update_stock": 1,
			"is_return": 1 if return_against else 0,
			"return_against": return_against,
			"items": [
				{
					"item_code": code,
					"qty": sign * qty,
					"rate": 1000,
					"warehouse": self.warehouse,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
				}
			],
			# one-shot submitted inserts skip ERPNext's payment-account defaulting: name the account
			"payments": [{"mode_of_payment": "Cash", "account": self.cash, "amount": sign * qty * 1000}],
		}

	def batch_qty(self, code, batch):
		rows = self.api.get_list(
			"Stock Ledger Entry",
			fields=["actual_qty", "serial_and_batch_bundle", "batch_no"],
			filters={"item_code": code, "warehouse": self.warehouse, "is_cancelled": 0},
		)
		return sum(r["actual_qty"] for r in rows)

	def batch_balance(self, batch):
		"""The batch's own on-hand quantity as ERPNext reports it (Batch.batch_qty)."""
		row = self.api.get_list("Batch", fields=["batch_qty"], filters={"name": batch})
		return row[0]["batch_qty"] if row else None

	def gl_balanced(self, voucher):
		rows = self.api.get_list(
			"GL Entry", fields=["debit", "credit"], filters={"voucher_no": voucher, "is_cancelled": 0}
		)
		return bool(rows) and round(sum(r["debit"] for r in rows) - sum(r["credit"] for r in rows), 2) == 0


def classify(response):
	if isinstance(response, tuple):
		return "exception", response[1]
	try:
		body = response.json()
	except Exception:
		body = {}
	exc = body.get("exc_type") or ""
	return response.status_code, exc


def check_returns(api, shop, rounds, mode):
	"""(sold, [return qtys]) races; only combinations within the sold quantity may succeed."""
	scenarios = [
		("A sold 1, 1+1", 1, [1, 1]),
		("B sold 2, 2+2", 2, [2, 2]),
		("C sold 4, 3+2", 4, [3, 2]),
		("D sold 3, 1+1+1+1", 3, [1, 1, 1, 1]),
	]
	failures = []
	for label, sold, returns in scenarios:
		for rnd in range(rounds):
			code, batch = shop.medicine(stock=10)
			sale = api.ok("frappe.client.insert", doc={**shop.sale_doc(code, batch, sold), "docstatus": 1})[
				"name"
			]
			check = shop.api
			after_sale = shop.batch_qty(code, batch)
			sessions = [api.session() for _ in returns]
			if mode == "submit":  # drafts saved first, then submitted at the same instant (POS flow)
				drafts = [
					api.ok("frappe.client.insert", doc=shop.sale_doc(code, batch, q, return_against=sale))
					for q in returns
				]
				calls = [
					lambda s=s, d=d: api.call("frappe.client.submit", session=s, doc=d)
					for s, d in zip(sessions, drafts, strict=True)
				]
			else:  # one-shot submitted inserts at the same instant
				calls = [
					lambda s=s, q=q: api.call(
						"frappe.client.insert",
						session=s,
						doc={**shop.sale_doc(code, batch, q, return_against=sale), "docstatus": 1},
					)
					for s, q in zip(sessions, returns, strict=True)
				]
			results = simultaneous(calls)
			outcome = [classify(r) for r in results]
			accepted = [q for q, (status, _exc) in zip(returns, outcome, strict=True) if status == 200]
			submitted = check.get_list(
				"Sales Invoice", fields=["name"], filters={"return_against": sale, "docstatus": 1}
			)
			returned_items = check.get_list(
				"Sales Invoice Item",
				fields=["qty"],
				parent="Sales Invoice",
				filters={"parent": ["in", [s["name"] for s in submitted] or [""]]},
			)
			returned = -sum(r["qty"] for r in returned_items)
			stock_now = shop.batch_qty(code, batch)
			problems = []
			if returned > sold:
				problems.append(f"returned {returned} > sold {sold} (double refund)")
			if sum(accepted) != returned:
				problems.append(f"accepted {accepted} but ledger shows {returned} returned")
			if stock_now != after_sale + returned:
				problems.append(f"stock {stock_now} != {after_sale}+{returned} (phantom stock)")
			if not accepted:
				problems.append("no return succeeded")
			# greedy: a loser must not have fitted next to the winners (the check is not over-strict)
			for q, (status, _exc) in zip(returns, outcome, strict=True):
				if status != 200 and returned + q <= sold:
					problems.append(f"a return of {q} was refused although {sold - returned} remained")
			for status, exc in outcome:
				if status != 200 and not (status == 417 and "StockOverReturnError" in exc):
					problems.append(f"loser got {status} {exc} (not a controlled business error)")
			for s in submitted:
				if not shop.gl_balanced(s["name"]):
					problems.append(f"GL of {s['name']} not balanced")
			if not shop.gl_balanced(sale):
				problems.append(f"GL of sale {sale} not balanced")
			status = "FAIL" if problems else "ok"
			print(
				f"returns[{mode}] {label} round {rnd + 1}: {status} statuses={[o[0] for o in outcome]} returned={returned}/{sold} {problems or ''}"
			)
			if problems:
				failures.append((label, rnd, problems))
	return failures


def check_mixed(api, shop, rounds):
	"""Deadlock probes: a return racing the cancellation of an earlier return, and a return racing a
	counter sale of the same medicine. Every request ends in 200 or a business error; never a 5xx."""
	failures = []
	for rnd in range(rounds):
		code, batch = shop.medicine(stock=10)
		sale = api.ok("frappe.client.insert", doc={**shop.sale_doc(code, batch, 2), "docstatus": 1})["name"]
		first = api.ok(
			"frappe.client.insert", doc={**shop.sale_doc(code, batch, 2, return_against=sale), "docstatus": 1}
		)
		s1, s2, s3 = api.session(), api.session(), api.session()
		results = simultaneous(
			[
				lambda: api.call(
					"frappe.client.cancel", session=s1, doctype="Sales Invoice", name=first["name"]
				),
				lambda: api.call(
					"frappe.client.insert",
					session=s2,
					doc={**shop.sale_doc(code, batch, 2, return_against=sale), "docstatus": 1},
				),
				lambda: api.call(
					"frappe.client.insert", session=s3, doc={**shop.sale_doc(code, batch, 1), "docstatus": 1}
				),
			]
		)
		outcome = [classify(r) for r in results]
		submitted = shop.api.get_list(
			"Sales Invoice", fields=["name"], filters={"return_against": sale, "docstatus": 1}
		)
		items = shop.api.get_list(
			"Sales Invoice Item",
			fields=["qty"],
			parent="Sales Invoice",
			filters={"parent": ["in", [x["name"] for x in submitted] or [""]]},
		)
		returned = -sum(r["qty"] for r in items)
		problems = []
		if any(o[0] == "exception" or (isinstance(o[0], int) and o[0] >= 500) for o in outcome):
			problems.append("5xx/deadlock")
		if outcome[0][0] != 200 or outcome[2][0] != 200:
			problems.append("cancel and the unrelated counter sale must succeed")
		if returned > 2:
			problems.append(f"returned {returned} > sold 2")
		print(
			f"mixed round {rnd + 1}: {'FAIL' if problems else 'ok'} {[o[0] for o in outcome]} returned={returned}/2 {problems or ''}"
		)
		if problems:
			failures.append(("mixed", rnd, problems))
	return failures


# exception types a race loser may legitimately receive (HTTP 417 business errors, never 5xx)
CONTROLLED = {
	"StockOverReturnError",  # the sale's quantity is already returned
	"RefundExceedsSaleError",  # the sale's money is already refunded (round 4)
	"ReturnAgainstReturnError",  # the reference is a return, not the sale (round 4)
	"ReturnAgainstCancelledSaleError",  # the sale was cancelled while the return waited
	"CancelledLinkError",  # Frappe: the return links a sale it already sees cancelled
	"SaleHasActiveReturnsError",  # the sale has submitted returns
	"LinkExistsError",  # Frappe/ERPNext: submitted documents link to the sale
}
# a cancel that read the document before a concurrent change: Frappe refuses ("modified after you
# opened it, refresh"), nothing is changed — a controlled refusal, the retry then decides on the merits
STALE = "TimestampMismatchError"


def controlled(outcome, kind=None):
	status, exc = outcome
	if status != 417:
		return False
	return any(name in exc for name in CONTROLLED) or (kind in ("cancel", "return-cancel") and STALE in exc)


def delayed(seconds, fn):
	def run():
		if seconds:
			time.sleep(seconds)
		return fn()

	return run


def original_state(shop, code, batch, sale, sold, received):
	"""Every invariant of a sale and its returns, read back after a race. Returns a list of problems."""
	api = shop.api
	doc = api.ok("frappe.client.get", doctype="Sales Invoice", name=sale)
	returns = api.get_list(
		"Sales Invoice",
		fields=["name", "docstatus"],
		filters={"return_against": sale, "is_return": 1, "docstatus": ["<", 2]},
	)
	submitted = [r["name"] for r in returns if r["docstatus"] == 1]
	items = api.get_list(
		"Sales Invoice Item",
		fields=["qty"],
		parent="Sales Invoice",
		filters={"parent": ["in", submitted or [""]]},
	)
	returned = -sum(r["qty"] for r in items)
	problems = []
	active = doc["docstatus"] == 1
	if not active and submitted:
		problems.append(f"sale {sale} is cancelled but returns {submitted} are submitted")
	if returned > sold:
		problems.append(f"returned {returned} > sold {sold} (double refund)")
	expected = received - sold + returned if active else received
	stock = shop.batch_qty(code, batch)
	if stock != expected:
		problems.append(f"stock ledger {stock} != expected {expected} (phantom stock)")
	bins = api.get_list(
		"Bin", fields=["actual_qty"], filters={"item_code": code, "warehouse": shop.warehouse}
	)
	if not bins or bins[0]["actual_qty"] != expected:
		problems.append(f"Bin {bins} != expected {expected}")
	from_batch = shop.batch_balance(batch)
	if from_batch is not None and from_batch != expected:
		problems.append(f"batch quantity {from_batch} != expected {expected}")
	# GL: submitted vouchers balanced; nothing live for cancelled ones or against a cancelled sale
	for voucher in [sale, *submitted]:
		if (voucher == sale and active) or voucher != sale:
			if not shop.gl_balanced(voucher):
				problems.append(f"GL of {voucher} not balanced")
	if not active:
		live = api.get_list(
			"GL Entry",
			fields=["voucher_no"],
			filters={"is_cancelled": 0, "against_voucher": sale, "voucher_type": "Sales Invoice"},
		)
		live += api.get_list(
			"GL Entry", fields=["voucher_no"], filters={"is_cancelled": 0, "voucher_no": sale}
		)
		if live:
			problems.append(f"live GL against cancelled sale: {sorted({g['voucher_no'] for g in live})}")
	# return tracking ledger on the original == the submitted returns
	raw = doc.get("pharma_return_ledger")
	tracked = sorted(json.loads(raw)["returns"]) if raw else []
	if tracked != sorted(submitted):
		problems.append(f"return ledger {tracked} != submitted returns {sorted(submitted)}")
	return problems, {
		"sale": "active" if active else "cancelled",
		"returns": len(submitted),
		"returned": returned,
	}


def check_original_cancel(api, canceller, shop, rounds):
	"""A return (or several) racing the cancellation of the ORIGINAL sale — simultaneous and staggered.

	Whatever wins, the end state is consistent: either the sale is active with its returns, or it is
	cancelled and has no submitted return. The loser gets a controlled business error (417), and
	repeating the losing operation afterwards fails the same controlled way."""
	sold, received = 4, 10
	scenarios = [
		# label, return qtys, return delay, cancel delay, existing return to cancel in the race
		("simultaneous return vs cancel", [2], 0, 0, False),
		("staggered return -> cancel", [2], 0, "s", False),
		("staggered cancel -> return", [2], "s", 0, False),
		("several returns vs cancel", [2, 1, 1], 0, 0, False),
		("several returns staggered vs cancel", [2, 1, 1], "s", 0, False),
		("return cancel vs original cancel", [], 0, 0, True),
		("return cancel + new return vs original cancel", [1], 0, "s", True),
	]
	failures = []
	for label, qtys, return_delay, cancel_delay, with_existing in scenarios:
		for rnd in range(rounds):
			stagger = 0.03 * (rnd % 8)  # 0 … 210 ms: covers "before", "during" and "after" the winner
			rd = stagger if return_delay == "s" else return_delay
			cd = stagger if cancel_delay == "s" else cancel_delay
			code, batch = shop.medicine(stock=received)
			sale = api.ok("frappe.client.insert", doc={**shop.sale_doc(code, batch, sold), "docstatus": 1})[
				"name"
			]
			calls, kinds = [], []
			if with_existing:
				existing = api.ok(
					"frappe.client.insert",
					doc={**shop.sale_doc(code, batch, 1, return_against=sale), "docstatus": 1},
				)["name"]
				s = api.session()
				calls.append(
					delayed(
						rd,
						lambda s=s, n=existing: api.call(
							"frappe.client.cancel", session=s, doctype="Sales Invoice", name=n
						),
					)
				)
				kinds.append("return-cancel")
			for q in qtys:
				s = api.session()
				calls.append(
					delayed(
						rd,
						lambda s=s, q=q: api.call(
							"frappe.client.insert",
							session=s,
							doc={**shop.sale_doc(code, batch, q, return_against=sale), "docstatus": 1},
						),
					)
				)
				kinds.append("return")
			s = canceller.session()
			calls.append(
				delayed(
					cd,
					lambda s=s: canceller.call(
						"frappe.client.cancel", session=s, doctype="Sales Invoice", name=sale
					),
				)
			)
			kinds.append("cancel")
			outcome = [classify(r) for r in simultaneous(calls)]
			problems = []
			for kind, o in zip(kinds, outcome, strict=True):
				if o[0] != 200 and not controlled(o, kind):
					problems.append(f"{kind} got {o[0]} {o[1]} (not a controlled business error)")
			state_problems, state = original_state(shop, code, batch, sale, sold, received)
			problems += state_problems
			# repeated attempts after one side won: the loser's operation fails the same controlled way
			# (a sale left with no returns can then be cancelled normally)
			for _ in range(2):
				if state["sale"] == "cancelled":
					again = classify(
						api.call(
							"frappe.client.insert",
							doc={**shop.sale_doc(code, batch, 1, return_against=sale), "docstatus": 1},
						)
					)
				elif state["returns"]:
					again = classify(
						canceller.call("frappe.client.cancel", doctype="Sales Invoice", name=sale)
					)
				else:
					again = classify(
						canceller.call("frappe.client.cancel", doctype="Sales Invoice", name=sale)
					)
					if again[0] != 200:
						problems.append(f"cancelling the sale without returns got {again}")
					state = original_state(shop, code, batch, sale, sold, received)[1]
					continue
				if not any(name in again[1] for name in CONTROLLED) or again[0] != 417:
					problems.append(f"retry after the race got {again}")
			problems += original_state(shop, code, batch, sale, sold, received)[0]
			print(
				f"original_cancel {label} round {rnd + 1}: {'FAIL' if problems else 'ok'} "
				f"{dict(zip(kinds, [o[0] if o[0] == 200 else f'{o[0]} {o[1]}' for o in outcome], strict=True))} {state} {problems or ''}"
			)
			if problems:
				failures.append((f"original_cancel:{label}", rnd, problems))
	return failures


def check_unrelated_cancel(api, canceller, shop, rounds):
	"""Round 4 (D-5): a counter sale of medicine X — submitted, and a draft (the POS screen save) —
	racing the cancellation of a return of medicine Y, and the same against the cancellation of a sale
	of Y, and against a cancellation of X's own earlier return. Without an index on the batch bundle
	tables the cancellation locked every item's batch entries and one side died with HTTP 508."""
	failures = []
	for rnd in range(rounds):
		x, xb = shop.medicine(stock=10)
		y, yb = shop.medicine(stock=10)
		ysale = api.ok("frappe.client.insert", doc={**shop.sale_doc(y, yb, 2), "docstatus": 1})["name"]
		yret = api.ok(
			"frappe.client.insert", doc={**shop.sale_doc(y, yb, 2, return_against=ysale), "docstatus": 1}
		)["name"]
		xsale = api.ok("frappe.client.insert", doc={**shop.sale_doc(x, xb, 1), "docstatus": 1})["name"]
		xret = api.ok(
			"frappe.client.insert", doc={**shop.sale_doc(x, xb, 1, return_against=xsale), "docstatus": 1}
		)["name"]
		victim = [yret, ysale, xret][rnd % 3]
		s1, s2, s3 = api.session(), canceller.session(), api.session()
		outcome = [
			classify(r)
			for r in simultaneous(
				[
					lambda s=s1: api.call(
						"frappe.client.insert", session=s, doc={**shop.sale_doc(x, xb, 1), "docstatus": 1}
					),
					lambda s=s2: canceller.call(
						"frappe.client.cancel", session=s, doctype="Sales Invoice", name=victim
					),
					lambda s=s3: api.call("frappe.client.insert", session=s, doc=shop.sale_doc(x, xb, 1)),
				]
			)
		]
		problems = []
		for kind, o in zip(("sale", "cancel", "draft"), outcome, strict=True):
			if o[0] != 200 and not (o[0] == 417 and any(n in o[1] for n in CONTROLLED)):
				problems.append(f"{kind} got {o[0]} {o[1]} (not a controlled business error)")
		if victim == yret and outcome[1][0] == 200 and outcome[0][0] != 200:
			problems.append("the sale of an unrelated medicine must succeed")
		x_stock = shop.batch_qty(x, xb)
		expected_x = 10 - 1 + 1 - (1 if outcome[0][0] == 200 else 0)
		if victim == xret and outcome[1][0] == 200:
			expected_x -= 1  # the cancelled return took its unit back out
		if x_stock != expected_x:
			problems.append(f"stock of X {x_stock} != expected {expected_x}")
		for voucher in (xsale, ysale):
			if not shop.gl_balanced(voucher):
				problems.append(f"GL of {voucher} not balanced")
		print(
			f"unrelated_cancel round {rnd + 1} ({'Y return' if victim == yret else 'Y sale' if victim == ysale else 'X return'}): "
			f"{'FAIL' if problems else 'ok'} {[o[0] for o in outcome]} {problems or ''}"
		)
		if problems:
			failures.append(("unrelated_cancel", rnd, problems))
	return failures


def check_last_unit(api, shop, rounds):
	failures = []
	for rnd in range(rounds):
		code, batch = shop.medicine(stock=1)
		sessions = [api.session(), api.session()]

		def sell(s, code=code, batch=batch):
			draft = api.call("frappe.client.insert", session=s, doc=shop.sale_doc(code, batch, 1))
			if draft.status_code != 200:
				return draft
			return api.call("frappe.client.submit", session=s, doc=draft.json()["message"])

		outcome = [classify(r) for r in simultaneous([lambda s=s: sell(s) for s in sessions])]
		sold = shop.api.get_list(
			"Sales Invoice Item",
			fields=["parent"],
			parent="Sales Invoice",
			filters={"item_code": code, "docstatus": 1},
		)
		problems = []
		if len(sold) != 1:
			problems.append(f"{len(sold)} sales of the last unit")
		if [o[0] for o in outcome].count(200) != 1:
			problems.append("exactly one request must succeed")
		if any(o[0] == "exception" or (isinstance(o[0], int) and o[0] >= 500) for o in outcome):
			problems.append("5xx/deadlock")
		if shop.batch_qty(code, batch) != 0:
			problems.append("stock not zero")
		print(f"last_unit round {rnd + 1}: {'FAIL' if problems else 'ok'} {outcome} {problems or ''}")
		if problems:
			failures.append(("last_unit", rnd, problems))
	return failures


def check_checkout(api, integration, shop, rounds):
	failures = []
	for rnd in range(rounds):
		code, _batch = shop.medicine(stock=3)
		shop.api.ok(
			"frappe.client.set_value", doctype="Item", name=code, fieldname="pharmacyos_publish", value=1
		)
		body = {
			"order_id": f"CC-ORDER-{uuid.uuid4().hex[:8]}",
			"branch_code": shop.branch_code,
			"items": [{"item_code": code, "qty": 1}],
			"customer": {"id": f"cc-{rnd}", "name": "Concurrency"},
		}
		sessions = [integration.session() for _ in range(4)]
		results = simultaneous(
			[
				lambda s=s: integration.call("pharmacyos_erp.api.v1.orders.create_order", session=s, **body)
				for s in sessions
			]
		)
		statuses = [classify(r)[0] for r in results]
		orders = {
			r.json()["message"]["sales_order"]
			for r in results
			if not isinstance(r, tuple) and r.status_code == 200
		}
		created = sum(
			1
			for r in results
			if not isinstance(r, tuple) and r.status_code == 200 and r.json()["message"]["created"]
		)
		count = len(shop.api.get_list("Sales Order", filters={"pharmacyos_order_id": body["order_id"]}))
		problems = []
		if statuses != [200] * 4:
			problems.append(f"statuses {statuses}")
		if len(orders) != 1 or created != 1 or count != 1:
			problems.append(f"orders={orders} created={created} count={count}")
		print(f"checkout round {rnd + 1}: {'FAIL' if problems else 'ok'} {statuses} {problems or ''}")
		if problems:
			failures.append(("checkout", rnd, problems))
	return failures


def main():
	p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
	p.add_argument("--url", default="http://127.0.0.1:8000")
	p.add_argument("--site", required=True)
	p.add_argument("--key", required=True)
	p.add_argument("--secret", required=True)
	p.add_argument("--setup-key", help="API user for discovery and test data (default: --key)")
	p.add_argument("--setup-secret")
	p.add_argument("--integration-key")
	p.add_argument("--integration-secret")
	p.add_argument("--rounds", type=int, default=8)
	p.add_argument("--canceller-key", help="user who cancels original sales (default: --key)")
	p.add_argument("--canceller-secret")
	p.add_argument("--only", default="returns,mixed,original_cancel,unrelated_cancel,last_unit,checkout")
	args = p.parse_args()
	api = Api(args.url, args.site, args.key, args.secret)
	setup = Api(args.url, args.site, args.setup_key, args.setup_secret) if args.setup_key else api
	shop = Shop(setup)
	only = set(args.only.split(","))
	failures = []
	if "returns" in only:
		failures += check_returns(api, shop, args.rounds, "insert")
		failures += check_returns(api, shop, args.rounds, "submit")
	if "mixed" in only:
		failures += check_mixed(api, shop, args.rounds)
	if "original_cancel" in only:
		canceller = (
			Api(args.url, args.site, args.canceller_key, args.canceller_secret) if args.canceller_key else api
		)
		failures += check_original_cancel(api, canceller, shop, args.rounds)
	if "unrelated_cancel" in only:
		canceller = (
			Api(args.url, args.site, args.canceller_key, args.canceller_secret) if args.canceller_key else api
		)
		failures += check_unrelated_cancel(api, canceller, shop, args.rounds)
	if "last_unit" in only:
		failures += check_last_unit(api, shop, args.rounds)
	if "checkout" in only and args.integration_key:
		integration = Api(args.url, args.site, args.integration_key, args.integration_secret)
		failures += check_checkout(api, integration, shop, args.rounds)
	print(json.dumps({"failures": len(failures), "by_check": Counter(f[0] for f in failures)}, default=str))
	sys.exit(1 if failures else 0)


if __name__ == "__main__":
	main()
