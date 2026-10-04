# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""N-01: returns against one sale can never exceed it — also under genuinely concurrent requests.

Two returns submitted at the same instant used to both succeed (double refund, phantom stock): the
second waited for the first's lock on the original sale, but then read the already-returned quantity
from its REPEATABLE READ snapshot, taken before the first committed. These tests use real requests,
each on its own database connection (Frappe's request handler in parallel threads), and one
deterministic test that reproduces the stale snapshot exactly.

`pharmacyos/dev/concurrency_check.py` runs the same races against a multi-worker gunicorn server.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, get_test_client

from pharmacyos_erp.tests.test_remediation import ensure_cash_mode, sale_doc
from pharmacyos_erp.tests.utils import WAREHOUSE, make_batch, make_medicine, receive

CASH = "Cash - _TC"


def submitted_doc(item_code, qty, batch, return_against=None):
	"""A sale or return posted in one request (docstatus 1): names its cash account explicitly."""
	doc = sale_doc(item_code, qty, batch, is_return=bool(return_against), return_against=return_against)
	doc["payments"][0]["account"] = CASH
	return {**doc, "docstatus": 1}


def admin_client():
	from frappe.tests.test_api import make_request

	client = get_test_client()
	response = make_request(
		client.post, ("/api/method/login",), {"json": {"usr": "Administrator", "pwd": "admin"}}
	)
	assert response.status_code == 200, response.get_data(as_text=True)
	return client


def simultaneously(calls):
	"""Start every call at the same moment (one barrier), each request in its own thread.

	Frappe's ThreadWithReturnValue serves each request for the test site on the request's own
	database connection, exactly as a server worker would.
	"""
	import threading

	from frappe.tests.test_api import ThreadWithReturnValue

	barrier = threading.Barrier(len(calls))

	def run(call):
		barrier.wait()
		return call()

	threads = [ThreadWithReturnValue(target=run, args=(call,)) for call in calls]
	for thread in threads:
		thread.start()
	return [thread.join() for thread in threads]


def post(client, method, data):
	from frappe.tests.test_api import make_request

	return make_request(client.post, (f"/api/method/{method}",), {"json": data})


def batch_qty(batch):
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	return flt(get_batch_qty(batch, WAREHOUSE))


class TestConcurrentReturns(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()
		frappe.db.commit()  # requests run on their own database connections

	def new_sale(self, sold):
		tag = frappe.generate_hash(length=6).upper()
		item = make_medicine(f"RACE-RET-{tag}")
		batch = make_batch(item.name, f"RR-{tag}", 300)
		receive(item.name, batch, 10)
		sale = frappe.get_doc(submitted_doc(item.name, sold, batch))
		sale.insert()
		frappe.db.commit()
		return item.name, batch, sale.name

	def race(self, sold, returns, mode):
		item, batch, sale = self.new_sale(sold)
		after_sale = batch_qty(batch)
		clients = [admin_client() for _ in returns]
		if mode == "submit":  # drafts saved first, then submitted at the same instant (the POS flow)
			drafts = []
			for qty in returns:
				doc = frappe.get_doc(sale_doc(item, qty, batch, is_return=True, return_against=sale))
				doc.insert()
				drafts.append(doc.as_dict(convert_dates_to_str=True))
			frappe.db.commit()
			calls = [
				lambda c=c, d=d: c.post("/api/method/frappe.client.submit", json={"doc": d})
				for c, d in zip(clients, drafts, strict=True)
			]  # (inside the request threads: the site is set by ThreadWithReturnValue)
		else:  # each return posted already submitted, at the same instant
			calls = [
				lambda c=c, q=q: c.post(
					"/api/method/frappe.client.insert",
					json={"doc": submitted_doc(item, q, batch, return_against=sale)},
				)
				for c, q in zip(clients, returns, strict=True)
			]
		responses = simultaneously(calls)
		frappe.db.rollback()  # a fresh snapshot: see what the requests committed

		statuses = [r.status_code for r in responses]
		accepted = sum(q for q, r in zip(returns, responses, strict=True) if r.status_code == 200)
		names = frappe.get_all(
			"Sales Invoice", filters={"return_against": sale, "docstatus": 1, "is_return": 1}, pluck="name"
		)
		returned = -sum(
			flt(q)
			for q in frappe.get_all(
				"Sales Invoice Item", filters={"parent": ["in", names or [""]]}, pluck="qty"
			)
		)
		context = f"sold {sold}, returns {returns} ({mode}): statuses {statuses}"
		self.assertLessEqual(returned, sold, f"double refund — {context}")
		self.assertEqual(accepted, returned, context)
		self.assertTrue(accepted, f"no return went through — {context}")
		for qty, response in zip(returns, responses, strict=True):
			if response.status_code != 200:
				# the loser gets the ordinary business error, never a 5xx or a deadlock
				self.assertEqual(
					response.status_code, 417, f"{context}: {response.get_data(as_text=True)[:300]}"
				)
				self.assertIn("StockOverReturnError", response.get_data(as_text=True))
				self.assertGreater(returned + qty, sold, f"a return that fitted was refused — {context}")
		self.assertEqual(batch_qty(batch), after_sale + returned, f"phantom stock — {context}")
		for name in [*names, sale]:
			gl = frappe.get_all(
				"GL Entry", filters={"voucher_no": name, "is_cancelled": 0}, fields=["debit", "credit"]
			)
			self.assertAlmostEqual(sum(g.debit for g in gl), sum(g.credit for g in gl), msg=f"GL of {name}")

		from pharmacyos_erp.pharmacy.returns import compute_ledger

		stored = json.loads(frappe.db.get_value("Sales Invoice", sale, "pharma_return_ledger") or "{}")
		self.assertEqual(stored, compute_ledger("Sales Invoice", sale), "ledger out of step with the returns")

	def test_sold_1_two_simultaneous_returns_of_1(self):
		for mode in ("insert", "submit", "insert"):
			self.race(1, [1, 1], mode)

	def test_sold_2_two_simultaneous_returns_of_2(self):
		for mode in ("insert", "submit", "submit"):
			self.race(2, [2, 2], mode)

	def test_sold_4_simultaneous_returns_of_3_and_2(self):
		for mode in ("insert", "submit", "insert"):
			self.race(4, [3, 2], mode)

	def test_sold_3_four_simultaneous_returns_of_1(self):
		self.race(3, [1, 1, 1, 1], "insert")
		self.race(3, [1, 1, 1, 1], "submit")

	def test_return_after_a_stale_snapshot_sees_the_committed_return(self):
		"""The exact failure: this transaction's snapshot predates a return another request committed."""
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		item, batch, sale = self.new_sale(2)
		# take this transaction's REPEATABLE READ snapshot now
		frappe.db.sql("select name from `tabSales Invoice` where name=%s", sale)
		other = post(
			admin_client(),
			"frappe.client.insert",
			{"doc": submitted_doc(item, 2, batch, return_against=sale)},
		)
		self.assertEqual(other.status_code, 200, other.get_data(as_text=True)[:300])
		# plain reads here still cannot see it ...
		self.assertFalse(frappe.db.exists("Sales Invoice", {"return_against": sale, "docstatus": 1}))
		# ... but the return check reads the ledger with a locking read, which can
		with self.assertRaises(StockOverReturnError):
			frappe.get_doc(submitted_doc(item, 1, batch, return_against=sale)).insert()
		frappe.db.rollback()


class TestReturnLedger(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()

	def setUp(self):
		tag = frappe.generate_hash(length=6).upper()
		self.item = make_medicine(f"RET-LEDGER-{tag}").name
		self.batch = make_batch(self.item, f"RL-{tag}", 300)
		receive(self.item, self.batch, 20)

	def post(self, qty, return_against=None):
		doc = frappe.get_doc(submitted_doc(self.item, qty, self.batch, return_against=return_against))
		doc.insert()
		return doc

	def ledger(self, sale):
		return json.loads(frappe.db.get_value("Sales Invoice", sale, "pharma_return_ledger") or "null")

	def test_sale_4_return_1_return_3_then_nothing(self):
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		sale = self.post(4).name
		first = self.post(1, sale).name
		second = self.post(3, sale).name
		with self.assertRaises(StockOverReturnError):
			self.post(1, sale)
		ledger = self.ledger(sale)
		self.assertEqual(set(ledger["returns"]), {first, second})
		self.assertEqual(sum(e["items"][self.item] for e in ledger["returns"].values()), 4)
		self.assertEqual(sum(e["batches"][self.item][self.batch] for e in ledger["returns"].values()), 4)

	def test_cancelled_return_gives_its_quantity_back(self):
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		sale = self.post(2).name
		ret = self.post(2, sale)
		ret.cancel()
		self.assertEqual(self.ledger(sale)["returns"], {})
		again = self.post(2, sale).name
		self.assertEqual(set(self.ledger(sale)["returns"]), {again})
		with self.assertRaises(StockOverReturnError):
			self.post(1, sale)

	def test_rolled_back_return_leaves_no_trace(self):
		sale = self.post(2).name
		frappe.db.savepoint("before_return")
		self.post(2, sale)
		frappe.db.rollback(save_point="before_return")
		self.assertIsNone(self.ledger(sale))
		self.post(2, sale)  # the full quantity is still returnable

	def test_upgrade_fills_the_ledger_from_existing_returns(self):
		from pharmacyos_erp.patches.v1 import return_ledger

		sale = self.post(3).name
		ret = self.post(1, sale).name
		frappe.db.set_value("Sales Invoice", sale, "pharma_return_ledger", None, update_modified=False)
		return_ledger.execute()
		self.assertEqual(set(self.ledger(sale)["returns"]), {ret})

	def test_original_without_a_ledger_is_computed_from_its_returns(self):
		"""Returns submitted before this version (no ledger yet) still count."""
		from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

		sale = self.post(2).name
		self.post(2, sale)
		frappe.db.set_value("Sales Invoice", sale, "pharma_return_ledger", None, update_modified=False)
		with self.assertRaises(StockOverReturnError):
			self.post(1, sale)

	def test_cancel_of_a_return_locks_the_original_first(self):
		"""Cancelling a return takes the same locks, in the same order, as submitting one."""
		from pharmacyos_erp.pharmacy import stock_guard

		sale = self.post(2).name
		ret = self.post(1, sale)
		ret.docstatus = 2
		statements = []
		real = frappe.db.sql

		def spy(query, *args, **kwargs):
			statements.append(" ".join(str(query).split()))
			return real(query, *args, **kwargs)

		from unittest.mock import patch

		with patch.object(frappe.db, "sql", side_effect=spy):
			stock_guard.lock_document_stock(ret)
		bins = [i for i, q in enumerate(statements) if "from `tabBin`" in q and "for update" in q]
		original = [
			i for i, q in enumerate(statements) if "from `tabSales Invoice` where name=%s for update" in q
		]
		self.assertTrue(bins and original, statements)
		self.assertLess(max(bins), min(original), "Bin rows first, then the original sale")


class TestOriginalSaleCancellation(IntegrationTestCase):
	"""A return racing the cancellation of the ORIGINAL sale.

	Before: both could commit — the sale cancelled (its stock and GL reversed) while the return stayed
	submitted (stock back on the shelf, a live refund against a reversed sale): 10 received, 4 sold,
	2 returned, sale cancelled → 12 on hand. Frappe's back-link check on cancel and ERPNext's "return
	against must be submitted" check both read the REPEATABLE READ snapshot. Now both sides decide from
	locking reads under the original's row lock (`returns.guard_original_cancellation`,
	`returns.ensure_original_submitted`).
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()
		frappe.db.commit()  # requests run on their own database connections

	def new_sale(self, sold=4, received=10):
		tag = frappe.generate_hash(length=6).upper()
		item = make_medicine(f"RACE-CXL-{tag}")
		batch = make_batch(item.name, f"RC-{tag}", 300)
		receive(item.name, batch, received)
		sale = frappe.get_doc(submitted_doc(item.name, sold, batch))
		sale.insert()
		frappe.db.commit()
		return item.name, batch, sale.name

	def assert_consistent(self, item, batch, sale, sold=4, received=10):
		"""The invariant: either an active sale with its returns, or a cancelled sale with none."""
		frappe.db.rollback()  # a fresh snapshot: see what the requests committed
		active = frappe.db.get_value("Sales Invoice", sale, "docstatus") == 1
		returns = frappe.get_all(
			"Sales Invoice", filters={"return_against": sale, "is_return": 1, "docstatus": 1}, pluck="name"
		)
		returned = -sum(
			flt(q)
			for q in frappe.get_all(
				"Sales Invoice Item", filters={"parent": ["in", returns or [""]]}, pluck="qty"
			)
		)
		if not active:
			self.assertEqual(returns, [], f"{sale} cancelled with submitted returns {returns}")
		self.assertLessEqual(returned, sold, "double refund")
		expected = received - sold + returned if active else received
		self.assertEqual(batch_qty(batch), expected, "phantom stock")
		self.assertEqual(
			flt(frappe.db.get_value("Bin", {"item_code": item, "warehouse": WAREHOUSE}, "actual_qty")),
			expected,
		)
		for name in returns + ([sale] if active else []):
			gl = frappe.get_all(
				"GL Entry", filters={"voucher_no": name, "is_cancelled": 0}, fields=["debit", "credit"]
			)
			self.assertTrue(gl, name)
			self.assertAlmostEqual(sum(g.debit for g in gl), sum(g.credit for g in gl), msg=f"GL of {name}")
		if not active:
			live = frappe.get_all(
				"GL Entry",
				filters={"is_cancelled": 0, "against_voucher": sale, "voucher_type": "Sales Invoice"},
				pluck="voucher_no",
			)
			self.assertEqual(live, [], "live GL against a cancelled sale")
		raw = frappe.db.get_value("Sales Invoice", sale, "pharma_return_ledger")
		tracked = sorted(json.loads(raw)["returns"]) if raw else []
		self.assertEqual(tracked, sorted(returns), "return ledger out of step")
		return active, returns

	def assert_controlled(self, response, *names):
		body = response.get_data(as_text=True)
		self.assertEqual(response.status_code, 417, body[:400])
		self.assertTrue(any(n in body for n in names), body[:400])

	def test_cancel_after_a_stale_snapshot_sees_the_committed_return(self):
		from pharmacyos_erp.pharmacy.returns import SaleHasActiveReturnsError

		item, batch, sale = self.new_sale()
		doc = frappe.get_doc("Sales Invoice", sale)  # this transaction's snapshot: no return yet
		other = post(
			admin_client(),
			"frappe.client.insert",
			{"doc": submitted_doc(item, 2, batch, return_against=sale)},
		)
		self.assertEqual(other.status_code, 200, other.get_data(as_text=True)[:300])
		self.assertFalse(frappe.db.exists("Sales Invoice", {"return_against": sale, "docstatus": 1}))
		with self.assertRaises(SaleHasActiveReturnsError):
			doc.cancel()
		self.assert_consistent(item, batch, sale)

	def test_return_after_a_stale_snapshot_sees_the_committed_cancellation(self):
		from pharmacyos_erp.pharmacy.returns import ReturnAgainstCancelledSaleError

		item, batch, sale = self.new_sale()
		frappe.db.sql("select name from `tabSales Invoice` where name=%s", sale)  # take the snapshot now
		other = post(admin_client(), "frappe.client.cancel", {"doctype": "Sales Invoice", "name": sale})
		self.assertEqual(other.status_code, 200, other.get_data(as_text=True)[:300])
		self.assertEqual(frappe.db.get_value("Sales Invoice", sale, "docstatus"), 1)  # snapshot: still active
		with self.assertRaises(ReturnAgainstCancelledSaleError):
			frappe.get_doc(submitted_doc(item, 2, batch, return_against=sale)).insert()
		self.assert_consistent(item, batch, sale)

	def test_sale_with_returns_cannot_be_cancelled_until_they_are(self):
		from pharmacyos_erp.pharmacy.returns import SaleHasActiveReturnsError

		item, batch, sale = self.new_sale()
		ret = frappe.get_doc(submitted_doc(item, 1, batch, return_against=sale))
		ret.insert()
		with self.assertRaises(SaleHasActiveReturnsError):
			frappe.get_doc("Sales Invoice", sale).cancel()
		ret.reload()
		ret.cancel()
		frappe.get_doc("Sales Invoice", sale).cancel()
		frappe.db.commit()
		self.assertEqual(self.assert_consistent(item, batch, sale), (False, []))

	def race(self, returns=(2,), cancel_existing=False, stagger=0.0, cancel_first=False):
		import time

		item, batch, sale = self.new_sale()
		calls, kinds = [], []

		def later(seconds, fn):
			def run():
				time.sleep(seconds)
				return fn()

			return run

		return_delay, cancel_delay = (stagger, 0.0) if cancel_first else (0.0, stagger)
		if cancel_existing:
			existing = frappe.get_doc(submitted_doc(item, 1, batch, return_against=sale))
			existing.insert()
			frappe.db.commit()
			c = admin_client()
			calls.append(
				later(
					return_delay,
					lambda c=c, n=existing.name: post(
						c, "frappe.client.cancel", {"doctype": "Sales Invoice", "name": n}
					),
				)
			)
			kinds.append("return-cancel")
		for qty in returns:
			c = admin_client()
			calls.append(
				later(
					return_delay,
					lambda c=c, q=qty: post(
						c, "frappe.client.insert", {"doc": submitted_doc(item, q, batch, return_against=sale)}
					),
				)
			)
			kinds.append("return")
		c = admin_client()
		calls.append(
			later(
				cancel_delay,
				lambda c=c: post(c, "frappe.client.cancel", {"doctype": "Sales Invoice", "name": sale}),
			)
		)
		kinds.append("cancel")
		responses = simultaneously(calls)
		for kind, response in zip(kinds, responses, strict=True):
			if response.status_code != 200:
				names = [
					"StockOverReturnError",
					"ReturnAgainstCancelledSaleError",
					"CancelledLinkError",
					"SaleHasActiveReturnsError",
					"LinkExistsError",
				]
				if kind != "return":
					names.append("TimestampMismatchError")  # stale form: nothing changed, retry decides
				self.assert_controlled(response, *names)
		active, submitted = self.assert_consistent(item, batch, sale)
		# repeating the losing side afterwards fails the same controlled way
		if not active:
			again = post(
				admin_client(),
				"frappe.client.insert",
				{"doc": submitted_doc(item, 1, batch, return_against=sale)},
			)
			self.assert_controlled(again, "ReturnAgainstCancelledSaleError", "CancelledLinkError")
		elif submitted:
			again = post(admin_client(), "frappe.client.cancel", {"doctype": "Sales Invoice", "name": sale})
			self.assert_controlled(again, "SaleHasActiveReturnsError", "LinkExistsError")
		self.assert_consistent(item, batch, sale)

	def test_return_vs_original_cancel_simultaneous(self):
		for _ in range(3):
			self.race()

	def test_return_vs_original_cancel_staggered_both_ways(self):
		for stagger in (0.05, 0.15):
			self.race(stagger=stagger)
			self.race(stagger=stagger, cancel_first=True)

	def test_several_returns_vs_original_cancel(self):
		self.race(returns=(2, 1, 1))
		self.race(returns=(2, 1, 1), stagger=0.1, cancel_first=True)

	def test_return_cancellation_vs_original_cancel(self):
		# (the three-way race "return cancel + new return vs original cancel" runs in
		# pharmacyos/dev/concurrency_check.py against gunicorn: under tests ERPNext reposts stock inline,
		# inside the cancelling request's own stale snapshot, which a real server does in a background job)
		self.race(returns=(), cancel_existing=True)
		self.race(returns=(), cancel_existing=True, stagger=0.1)
		self.race(returns=(), cancel_existing=True, stagger=0.1, cancel_first=True)

	def test_cancelling_an_original_locks_bins_then_the_sale(self):
		from unittest.mock import patch

		from pharmacyos_erp.pharmacy import stock_guard

		_item, _batch, sale = self.new_sale()
		doc = frappe.get_doc("Sales Invoice", sale)
		doc.docstatus = 2
		statements = []
		real = frappe.db.sql

		def spy(query, *args, **kwargs):
			statements.append(" ".join(str(query).split()))
			return real(query, *args, **kwargs)

		with patch.object(frappe.db, "sql", side_effect=spy):
			stock_guard.lock_document_stock(doc)
		bins = [i for i, q in enumerate(statements) if "from `tabBin`" in q and "for update" in q]
		own = [i for i, q in enumerate(statements) if "from `tabSales Invoice` where name=%s for update" in q]
		self.assertTrue(bins and own, statements)
		self.assertLess(max(bins), min(own), "Bin rows first, then the sale itself")
		frappe.db.rollback()
