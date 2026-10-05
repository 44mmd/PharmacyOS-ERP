# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Round 5 (B-2): a sale's returns never refund more money than the sale charged, however split.

Reproduced on the round-4 candidate (independent re-validation): 100 × 4.6 charged 460 (rounded
total). 100 single-unit returns were each rounded to 5 by ERPNext and refunded 500 in total: the
tolerance was per return document (half a unit of rounding each time) and the refunds were compared
before rounding, while the cash actually paid out was the rounded figure.

Here every pattern — splits of one sale into 1 … 100 returns, price boundaries around the rounding
step, row / document / percentage / fixed discounts, inclusive / exclusive / fractional taxes,
alternative and fractional units, cancellation, concurrency and field tampering — is checked against
the money that actually moved: the cash account in the general ledger.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, rounded

from pharmacyos_erp.pharmacy.returns import RefundExceedsSaleError, compute_ledger, returned_so_far
from pharmacyos_erp.tests.test_return_integrity_round4 import (
	CASH,
	ReturnCase,
	cash_credited,
	doc,
	over_return_error,
	post,
	stock,
)

TAX_ACCOUNT = "_Test Account Excise Duty - _TC"
COST_CENTER = "_Test Cost Center - _TC"
UNIT = 0.01  # the company currency's precision unit in the test company


def cash_debited(voucher):
	return flt(
		frappe.db.get_value("GL Entry", {"voucher_no": voucher, "account": CASH, "is_cancelled": 0}, "debit")
	)


def effective(name) -> float:
	"""What a document charges or refunds: ERPNext's rounded total when it is rounded, else its total."""
	from pharmacyos_erp.pharmacy.returns import _doc_total

	return flt(_doc_total(frappe.get_doc("Sales Invoice", name)), 2)


def returns_of(sale):
	return frappe.get_all(
		"Sales Invoice", filters={"return_against": sale, "docstatus": 1, "is_return": 1}, pluck="name"
	)


def refunded(sale) -> float:
	"""What every submitted return of `sale` refunds (cash or credit)."""
	return flt(sum(effective(n) for n in returns_of(sale)), 2)


def cash_paid_out(sale) -> float:
	return flt(sum(cash_credited(n) for n in returns_of(sale)), 2)


def shown(d: dict) -> dict:
	"""The client pays (or pays out) what ERPNext shows for the document: its rounded total."""
	si = frappe.get_doc(d)
	si.calculate_taxes_and_totals()
	d["payments"][0]["amount"] = flt(si.rounded_total or si.grand_total)
	return d


def paid_sale(item, batch, qty, rate):
	return post(shown(doc(item, qty, batch, rate=rate)))


def make_return(sale_name, item, batch, qty, rate):
	return post(shown(doc(item, qty, batch, return_against=sale_name, rate=rate)))


def attempt(d):
	"""Post as one request would: a refused document leaves nothing behind (rolled back)."""
	frappe.db.savepoint("r5_attempt")
	try:
		return post(d)
	except frappe.ValidationError:
		frappe.db.rollback(save_point="r5_attempt")
		frappe.clear_messages()
		raise


def make_sales_return(name):
	try:
		from erpnext.accounts.doctype.sales_invoice.mapper import make_sales_return as make
	except ImportError:
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return as make
	return make(name)


def mapped_return(sale, qty_by_row: list[float], proportional_discount=False):
	"""ERPNext's own Return button: mapped from the sale, quantities set, totals recomputed, the
	cashier pays out the recomputed total."""
	ret = make_sales_return(sale.name)
	for row, qty in zip(ret.items, qty_by_row, strict=True):
		row.qty = -qty
	ret.items = [row for row in ret.items if row.qty]
	if proportional_discount and flt(sale.discount_amount) and not flt(sale.additional_discount_percentage):
		sold = sum(flt(r.qty) for r in sale.items)
		ret.discount_amount = -flt(sale.discount_amount * sum(qty_by_row) / sold, 2)
	ret.calculate_taxes_and_totals()
	ret.payments[0].amount = flt(ret.rounded_total or ret.grand_total)
	ret.calculate_taxes_and_totals()
	ret.insert()
	ret.submit()
	return ret


class RefundCase(ReturnCase):
	def assert_never_over(self, sale, context=""):
		charged = effective(sale.name)
		paid_out = refunded(sale.name)
		self.assertLessEqual(
			paid_out, flt(charged + UNIT, 2), f"refunded {paid_out} on {charged} — {context}"
		)
		# the cash drawer never pays out more than the returns refund, nor more than it took in
		self.assertLessEqual(cash_paid_out(sale.name), flt(paid_out + UNIT, 2), context)
		self.assertLessEqual(cash_paid_out(sale.name), flt(cash_debited(sale.name) + UNIT, 2), context)
		stored = json.loads(frappe.db.get_value("Sales Invoice", sale.name, "pharma_return_ledger") or "{}")
		if stored:
			self.assertEqual(stored, compute_ledger("Sales Invoice", sale.name), f"ledger — {context}")
		return charged, paid_out


# --------------------------------------------------------------------------- the reproduction


class TestSplitEquivalence(RefundCase):
	"""100 × 4.6 returned in 1, 2, 4, 10, 20, 50 or 100 documents refunds exactly 460 every time."""

	def test_every_split_refunds_exactly_what_was_charged(self):
		for size in (100, 50, 25, 10, 5, 2, 1):
			with self.subTest(split=f"{100 // size} × {size}"):
				item, batch = self.medicine(100)
				sale = paid_sale(item, batch, 100, 4.6)
				self.assertEqual(cash_debited(sale.name), 460)
				for _ in range(100 // size):
					make_return(sale.name, item, batch, size, 4.6)
				_charged, paid_out = self.assert_never_over(sale, f"split {size}")
				self.assertEqual(paid_out, 460, f"split {size}")
				self.assertEqual(cash_paid_out(sale.name), 460, f"split {size}")
				self.assertEqual(stock(item), (100, 100))
				with self.assertRaises(over_return_error()):
					make_return(sale.name, item, batch, 1, 4.6)

	def test_the_independent_reproduction_payout_of_5_each(self):
		"""The client pays out 5 per return (ERPNext's rounded figure): the 100th return still ends at 460."""
		item, batch = self.medicine(100)
		sale = paid_sale(item, batch, 100, 4.6)
		for _ in range(100):
			ret = post(doc(item, 1, batch, return_against=sale.name, rate=4.6, payment=-5))
			self.assertIn(cash_credited(ret.name), (4, 5))  # round(4.6 k) − round(4.6 (k − 1))
		self.assertEqual(refunded(sale.name), 460)
		self.assertEqual(cash_paid_out(sale.name), 460)


# --------------------------------------------------------------------------- price boundaries


PRICES = (0.01, 0.1, 0.4, 0.49, 0.5, 0.51, 0.99, 1.4, 1.5, 1.6, 4.4, 4.5, 4.6, 0.125, 2.675, 7.333)
PATTERNS = {"one return": [10], "ten returns": [1] * 10, "three returns": [3, 3, 4]}


class TestPriceBoundaries(RefundCase):
	def test_prices_around_the_rounding_step(self):
		for price in PRICES:
			for pattern, sizes in PATTERNS.items():
				with self.subTest(price=price, pattern=pattern):
					item, batch = self.medicine(10)
					si = paid_sale(item, batch, 10, price)
					for size in sizes:
						make_return(si.name, item, batch, size, flt(si.items[0].rate))
					charged, paid_out = self.assert_never_over(si, f"{price} {pattern}")
					# split or not, everything returned refunds exactly what was charged
					self.assertAlmostEqual(paid_out, charged, delta=UNIT, msg=f"{price} {pattern}")
					self.assertEqual(stock(item), (10, 10))


# --------------------------------------------------------------------------- discounts and taxes


def discounted_sale(item, batch, kind, qty=10, rate=4.6, taxes=None):
	d = doc(item, qty, batch, rate=rate)
	row = d["items"][0]
	if kind == "row percentage":
		row.update({"rate": 0, "price_list_rate": rate, "discount_percentage": 10})
	elif kind == "row fixed":
		row.update({"rate": 0, "price_list_rate": rate, "discount_amount": 0.37})
	elif kind == "document percentage":
		d.update({"apply_discount_on": "Grand Total", "additional_discount_percentage": 7})
	elif kind == "document fixed":
		d.update({"apply_discount_on": "Grand Total", "discount_amount": 3.33})
	elif kind == "net total fixed":
		d.update({"apply_discount_on": "Net Total", "discount_amount": 3.33})
	if taxes:
		d["taxes"] = taxes
	si = frappe.get_doc(d)
	si.calculate_taxes_and_totals()
	si.payments[0].amount = flt(si.rounded_total or si.grand_total)
	si.insert()
	si.submit()
	return si


def tax(rate, included=0):
	return [
		{
			"charge_type": "On Net Total",
			"account_head": TAX_ACCOUNT,
			"cost_center": COST_CENTER,
			"description": f"Tax {rate}",
			"rate": rate,
			"included_in_print_rate": included,
		}
	]


DISCOUNTS = ("row percentage", "row fixed", "document percentage", "document fixed", "net total fixed")
TAXES = {
	"exclusive 10": tax(10),
	"inclusive 10": tax(10, 1),
	"fractional 7.5": tax(7.5),
	"fractional inclusive 2.5": tax(2.5, 1),
}


class TestDiscountsAndTaxes(RefundCase):
	def run_patterns(self, kind, taxes=None):
		for pattern, sizes in PATTERNS.items():
			for proportional in (False, True):
				with self.subTest(kind=kind, pattern=pattern, proportional=proportional):
					item, batch = self.medicine(10)
					sale = discounted_sale(item, batch, kind, taxes=taxes)
					for size in sizes:
						mapped_return(sale, [size], proportional_discount=proportional)
					charged, paid_out = self.assert_never_over(sale, f"{kind} {pattern}")
					self.assertEqual(stock(item), (10, 10))
					if proportional or "fixed" not in kind or "row" in kind or pattern == "one return":
						self.assertAlmostEqual(
							paid_out, charged, delta=UNIT, msg=f"{kind} {pattern} {proportional}"
						)

	def test_discounts(self):
		for kind in DISCOUNTS:
			self.run_patterns(kind)

	def test_taxes(self):
		for name, taxes in TAXES.items():
			self.run_patterns(f"none ({name})", taxes)

	def test_taxes_with_discounts(self):
		self.run_patterns("document percentage", tax(10))
		self.run_patterns("document fixed", tax(7.5))
		self.run_patterns("row percentage", tax(10, 1))


# --------------------------------------------------------------------------- units of measure


def uom(name, whole):
	if not frappe.db.exists("UOM", name):
		frappe.get_doc({"doctype": "UOM", "uom_name": name, "must_be_whole_number": whole}).insert()
	return name


class TestUnits(RefundCase):
	def test_alternative_uom_box_of_ten(self):
		box = uom("R5 Box", 1)
		item, batch = self.medicine(30)
		it = frappe.get_doc("Item", item)
		it.append("uoms", {"uom": box, "conversion_factor": 10})
		it.save()
		d = doc(item, 3, batch, rate=46.04)  # 3 boxes: 138.12 → 138
		d["items"][0].update({"uom": box, "conversion_factor": 10})
		sale = post(shown(d))
		for _ in range(3):  # returned in units of the stock UOM, 10 at a time
			make_return(sale.name, item, batch, 10, 4.604)
		charged, paid_out = self.assert_never_over(sale, "box")
		self.assertEqual(stock(item), (30, 30))
		self.assertAlmostEqual(paid_out, charged, delta=UNIT)

	def test_fractional_conversion_factor(self):
		fraction, strip = uom("R4 Fraction", 0), uom("R5 Strip", 1)
		item, batch = self.medicine(5, stock_uom=fraction)
		it = frappe.get_doc("Item", item)
		it.append("uoms", {"uom": strip, "conversion_factor": 0.1})
		it.save()
		sale = post(doc(item, 1, batch, rate=4.6, payment=5))  # 4.6 → 5 (rounded up)
		for _ in range(10):  # ten strips of 0.1 at 0.46
			r = doc(item, 1, batch, return_against=sale.name, rate=0.46)
			r["items"][0].update({"uom": strip, "conversion_factor": 0.1})
			post(shown(r))
		_charged, paid_out = self.assert_never_over(sale, "strips")
		self.assertEqual(paid_out, 5)
		self.assertEqual(stock(item), (5, 5))


# --------------------------------------------------------------------------- lifecycle


class TestLifecycle(RefundCase):
	def test_cancelling_returns_restores_exactly_their_refund(self):
		item, batch = self.medicine(10)
		sale = paid_sale(item, batch, 10, 4.6)
		returns = [make_return(sale.name, item, batch, 1, 4.6) for _ in range(5)]
		self.assertEqual(refunded(sale.name), 23)  # 5, 4, 5, 4, 5: round(4.6 k) after k returns
		cancelled = sum(effective(r.name) for r in returns[3:])
		for r in returns[3:]:  # the last two: 4 + 5
			frappe.get_doc("Sales Invoice", r.name).cancel()
		self.assertEqual(flt(returned_so_far("Sales Invoice", sale.name).total, 2), flt(23 - cancelled, 2))
		self.assertEqual(refunded(sale.name), 14)
		for _ in range(7):
			make_return(sale.name, item, batch, 1, 4.6)
		self.assertEqual(refunded(sale.name), 46)
		with self.assertRaises(over_return_error()):
			make_return(sale.name, item, batch, 1, 4.6)
		self.assert_never_over(sale, "after cancellations")
		self.assertEqual(stock(item), (10, 10))

	def test_sale_cancel_waits_for_its_returns(self):
		from pharmacyos_erp.pharmacy.returns import SaleHasActiveReturnsError

		item, batch = self.medicine(10)
		sale = paid_sale(item, batch, 10, 4.6)
		ret = make_return(sale.name, item, batch, 3, 4.6)
		with self.assertRaises(SaleHasActiveReturnsError):
			frappe.get_doc("Sales Invoice", sale.name).cancel()
		frappe.get_doc("Sales Invoice", ret.name).cancel()
		frappe.get_doc("Sales Invoice", sale.name).cancel()
		self.assertEqual(stock(item), (10, 10))

	def test_ledgers_written_before_round_5_are_rebuilt(self):
		from pharmacyos_erp.patches.v1 import return_ledger_effective_refunds

		item, batch = self.medicine(10)
		sale = paid_sale(item, batch, 10, 4.6)
		for _ in range(3):
			make_return(sale.name, item, batch, 1, 4.6)
		old = json.loads(frappe.db.get_value("Sales Invoice", sale.name, "pharma_return_ledger"))
		for entry in old["returns"].values():
			entry.pop("effective")
			entry["total"] = 5  # what a round-4 ledger may hold for a rounded return
		frappe.db.set_value("Sales Invoice", sale.name, "pharma_return_ledger", json.dumps(old))
		# read path: an old entry is recomputed, never trusted
		self.assertEqual(flt(returned_so_far("Sales Invoice", sale.name).total, 2), 14)  # 5 + 4 + 5
		frappe.db.set_value("Sales Invoice", sale.name, "pharma_return_ledger", json.dumps(old))
		return_ledger_effective_refunds.execute()
		stored = json.loads(frappe.db.get_value("Sales Invoice", sale.name, "pharma_return_ledger"))
		self.assertEqual(stored, compute_ledger("Sales Invoice", sale.name))
		return_ledger_effective_refunds.execute()  # idempotent
		self.assertEqual(
			json.loads(frappe.db.get_value("Sales Invoice", sale.name, "pharma_return_ledger")), stored
		)


class TestAsymmetricRounding(RefundCase):
	"""With a smallest currency fraction, ERPNext rounds 4.5 down to 4 but −4.5 up to −5."""

	def setUp(self):
		self.fraction = frappe.db.get_value("Currency", "INR", "smallest_currency_fraction_value")
		frappe.db.set_value("Currency", "INR", "smallest_currency_fraction_value", 1)
		frappe.clear_cache()

	def tearDown(self):
		frappe.db.set_value("Currency", "INR", "smallest_currency_fraction_value", self.fraction)
		frappe.clear_cache()

	def test_full_and_split_returns_of_a_sale_rounded_down(self):
		for sizes in ([3], [1, 1, 1], [2, 1]):
			with self.subTest(sizes=sizes):
				item, batch = self.medicine(3)
				sale = post(doc(item, 3, batch, rate=1.5, payment=4))
				self.assertEqual(cash_debited(sale.name), 4)
				for size in sizes:
					make_return(sale.name, item, batch, size, 1.5)
				_charged, paid_out = self.assert_never_over(sale, f"{sizes}")
				self.assertEqual(paid_out, 4, f"{sizes}: the customer gets back exactly what was paid")


# --------------------------------------------------------------------------- concurrency


class TestConcurrentSplitReturns(RefundCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.db.commit()

	def test_simultaneous_single_unit_returns(self):
		from pharmacyos_erp.tests.test_return_concurrency import admin_client, simultaneously

		item, batch = self.medicine(10)
		sale = paid_sale(item, batch, 10, 4.6)
		frappe.db.commit()
		clients = [admin_client() for _ in range(12)]
		body = {**doc(item, 1, batch, return_against=sale.name, rate=4.6, payment=-5), "docstatus": 1}
		responses = simultaneously(
			[lambda c=c: c.post("/api/method/frappe.client.insert", json={"doc": body}) for c in clients]
		)
		frappe.db.rollback()
		statuses = sorted(r.status_code for r in responses)
		bodies = [
			json.loads(json.loads(r.get_data(as_text=True))["exc"])[0][-1500:]
			for r in responses
			if r.status_code != 200
		]
		self.assertEqual(statuses.count(200), 10, f"{statuses} {bodies[:2]}")
		self.assertTrue(all(s == 417 for s in statuses if s != 200), statuses)
		self.assertEqual(refunded(sale.name), 46)
		self.assertEqual(cash_paid_out(sale.name), 46)
		self.assert_never_over(sale, "concurrent")
		self.assertEqual(stock(item), (10, 10))


# --------------------------------------------------------------------------- tampering


class TestTampering(RefundCase):
	def setUp(self):
		self.item, self.batch = self.medicine(10)
		self.sale = paid_sale(self.item, self.batch, 10, 4.6)

	def attempt(self, mutate, payment=-5):
		d = doc(self.item, 1, self.batch, return_against=self.sale.name, rate=4.6, payment=payment)
		mutate(d)
		try:
			attempt(d)
		except frappe.ValidationError:
			pass
		self.assert_never_over(self.sale, str(mutate))
		returned_qty = returned_so_far("Sales Invoice", self.sale.name).qty[self.item]
		self.assertLessEqual(
			refunded(self.sale.name), flt(rounded(4.6 * returned_qty) + UNIT, 2), str(mutate)
		)

	def test_field_tampering_never_pays_out_more(self):
		cases = {
			"rate": lambda d: d["items"][0].update({"rate": 5}),
			"price list + negative discount": lambda d: d["items"][0].update(
				{"rate": 0, "price_list_rate": 5, "discount_percentage": -8.7}
			),
			"amount": lambda d: d["items"][0].update({"amount": 50, "base_amount": 50}),
			"net amounts": lambda d: d["items"][0].update(
				{"net_rate": 50, "net_amount": 50, "base_net_amount": 50, "base_net_rate": 50}
			),
			"positive document discount": lambda d: d.update(
				{"apply_discount_on": "Grand Total", "discount_amount": 10}
			),
			"negative percentage": lambda d: d.update(
				{"apply_discount_on": "Grand Total", "additional_discount_percentage": -50}
			),
			"conversion factor": lambda d: d["items"][0].update({"conversion_factor": 10}),
			"unknown uom": lambda d: d["items"][0].update({"uom": "R5 Box", "conversion_factor": 10}),
			"tax row": lambda d: d.update({"taxes": tax(25)}),
			"inclusive tax row": lambda d: d.update({"taxes": tax(25, 1)}),
			"rounded totals": lambda d: d.update(
				{"rounded_total": -50, "base_rounded_total": -50, "rounding_adjustment": -45.4}
			),
			"rounding forced on": lambda d: d.update({"disable_rounded_total": 0}),
			"payout 50": lambda d: d["payments"][0].update({"amount": -50}),
			"write off": lambda d: d.update({"write_off_amount": 40, "base_write_off_amount": 40}),
			"second payment row": lambda d: d["payments"].append({"mode_of_payment": "Cash", "amount": -5}),
			"grand total": lambda d: d.update({"grand_total": -50, "base_grand_total": -50}),
		}
		uom("R5 Box", 1)
		for name, mutate in cases.items():
			with self.subTest(case=name):
				self.attempt(mutate)
		self.assertEqual(stock(self.item)[0], stock(self.item)[1])

	def test_references(self):
		other_item, other_batch = self.medicine(10)
		other = post(doc(other_item, 1, other_batch, rate=1, payment=1))
		with self.assertRaises(frappe.ValidationError):
			attempt(doc(self.item, 1, self.batch, return_against=other.name, rate=4.6, payment=-5))
		with self.assertRaises(frappe.ValidationError):  # a batch that was not sold
			attempt(doc(self.item, 1, other_batch, return_against=self.sale.name, rate=4.6, payment=-5))
		self.assertEqual(refunded(self.sale.name), 0)
		self.assertEqual(refunded(other.name), 0)
