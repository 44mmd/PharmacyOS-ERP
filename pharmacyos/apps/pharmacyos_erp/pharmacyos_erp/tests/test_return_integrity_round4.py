# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Round 4: every return counts against its root sale, in quantity *and* money (D-1, D-2).

Reproduced on the round-3 candidate before the fix (independent validation):

* D-1 — a return could reference another return. Six chained returns of 2 against a sale of 2
  (stock received 10) were accepted: stock went from 10 to 20 and 12 000 was refunded on a 2 000 sale,
  with every GL voucher balanced. The same through the POS flow (draft saved, then submitted) and
  through two returns against the same prior return.
* D-2 — the return check bounded quantities and the per-UOM rate, never money: a sale paid 1 000 after
  a document discount was refunded 2 000; a row-discounted sale paid 1 600 was refunded 2 000; a tax
  row added on the return refunded 2 500 on a 2 000 sale; a positive document discount on the return
  did the same; three "strip" returns (0.1 unit each, conversion 0.1) at the per-unit rate refunded
  3 000 on a 1 000 sale.

These tests fail on that implementation and pass with pharmacy/returns.py as it is now.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, get_test_client

from pharmacyos_erp.pharmacy.returns import (
	RefundExceedsSaleError,
	ReturnAgainstCancelledSaleError,
	ReturnAgainstReturnError,
	returned_so_far,
	root_sale,
)
from pharmacyos_erp.tests.test_remediation import ensure_cash_mode, sale_doc
from pharmacyos_erp.tests.utils import COMPANY, CUSTOMER, WAREHOUSE, make_batch, make_medicine, receive

CASH = "Cash - _TC"


def over_return_error():
	from erpnext.controllers.sales_and_purchase_return import StockOverReturnError

	return StockOverReturnError


def doc(item_code, qty, batch, return_against=None, rate=1000, payment=None, **extra):
	"""A paid counter sale, or a return against `return_against` (negative quantities)."""
	d = sale_doc(
		item_code, qty, batch, rate=rate, is_return=bool(return_against), return_against=return_against
	)
	d["payments"][0]["account"] = CASH
	if payment is not None:
		d["payments"][0]["amount"] = payment
	d.update(extra)
	return d


def post(d):
	si = frappe.get_doc(d)
	si.insert()
	si.submit()
	return si


def stock(item_code):
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={"item_code": item_code, "warehouse": WAREHOUSE, "is_cancelled": 0},
		pluck="actual_qty",
	)
	return flt(sum(flt(q) for q in rows), 6), flt(
		frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": WAREHOUSE}, "actual_qty")
	)


def cash_credited(voucher):
	return flt(
		frappe.db.get_value("GL Entry", {"voucher_no": voucher, "account": CASH, "is_cancelled": 0}, "credit")
	)


class ReturnCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_cash_mode()

	def medicine(self, stock=10, **kwargs):
		tag = frappe.generate_hash(length=6).upper()
		item = make_medicine(f"R4-RET-{tag}", **kwargs)
		batch = make_batch(item.name, f"R4B-{tag}", 300)
		receive(item.name, batch, stock)
		return item.name, batch


# --------------------------------------------------------------------------- D-1


class TestReturnChain(ReturnCase):
	def test_round3_chain_is_refused_and_leaves_no_phantom_stock(self):
		"""The exact independent reproduction: 10 received, 2 sold, returns chained one on the other."""
		item, batch = self.medicine(10)
		sale = post(doc(item, 2, batch))
		first = post(doc(item, 2, batch, return_against=sale.name))
		self.assertEqual(stock(item), (10, 10))
		for _ in range(5):
			with self.assertRaises(ReturnAgainstReturnError):
				post(doc(item, 2, batch, return_against=first.name))
		# the reference's root is named in the message, nothing was posted
		self.assertEqual(stock(item), (10, 10))
		returns = frappe.get_all(
			"Sales Invoice",
			filters={"is_return": 1, "docstatus": 1, "customer": CUSTOMER, "items.item_code": item},
			pluck="name",
		)
		self.assertEqual(returns, [first.name])
		self.assertEqual(cash_credited(first.name), 2000)
		# and nothing more can be returned against the sale either
		with self.assertRaises(over_return_error()):
			post(doc(item, 1, batch, return_against=sale.name))

	def test_chain_through_the_pos_flow_draft_then_submit(self):
		item, batch = self.medicine(10)
		sale = post(doc(item, 1, batch))
		first = post(doc(item, 1, batch, return_against=sale.name))
		with self.assertRaises(ReturnAgainstReturnError):  # refused already as a draft
			frappe.get_doc(doc(item, 1, batch, return_against=first.name)).insert()
		self.assertEqual(stock(item), (10, 10))

	def test_two_returns_against_the_same_prior_return(self):
		item, batch = self.medicine(10)
		sale = post(doc(item, 2, batch))
		first = post(doc(item, 2, batch, return_against=sale.name))
		for _ in range(2):
			with self.assertRaises(ReturnAgainstReturnError):
				post(doc(item, 2, batch, return_against=first.name))
		self.assertEqual(stock(item), (10, 10))

	def test_chain_over_http_is_a_controlled_business_error(self):
		from frappe.tests.test_api import make_request

		item, batch = self.medicine(10)
		sale = post(doc(item, 2, batch))
		first = post(doc(item, 2, batch, return_against=sale.name))
		frappe.db.commit()
		client = get_test_client()
		make_request(client.post, ("/api/method/login",), {"json": {"usr": "Administrator", "pwd": "admin"}})
		response = make_request(
			client.post,
			("/api/method/frappe.client.insert",),
			{"json": {"doc": {**doc(item, 2, batch, return_against=first.name), "docstatus": 1}}},
		)
		self.assertEqual(response.status_code, 417, response.get_data(as_text=True)[:300])
		self.assertIn("ReturnAgainstReturnError", response.get_data(as_text=True))
		frappe.db.rollback()
		self.assertEqual(stock(item), (10, 10))

	def test_root_sale_resolution(self):
		item, batch = self.medicine(10)
		sale = post(doc(item, 2, batch))
		first = post(doc(item, 1, batch, return_against=sale.name))
		self.assertEqual(root_sale("Sales Invoice", sale.name), (sale.name, []))
		self.assertEqual(root_sale("Sales Invoice", first.name), (sale.name, [first.name]))

	def test_historical_chain_counts_against_the_root_after_the_upgrade_patch(self):
		"""Data written before the rule: the patch rebuilds the root's ledger, the chain is kept untouched."""
		from pharmacyos_erp.patches.v1 import return_chain_integrity
		from pharmacyos_erp.pharmacy import returns

		item, batch = self.medicine(10)
		sale = post(doc(item, 2, batch))
		first = post(doc(item, 2, batch, return_against=sale.name))
		original = returns._check_reference
		returns._check_reference = lambda d: None  # the round-3 behaviour, to write the historical chain
		try:
			second = post(doc(item, 2, batch, return_against=first.name))
		finally:
			returns._check_reference = original
		self.assertEqual(stock(item), (12, 12))  # the historical phantom stock, as found on upgrade
		frappe.db.set_value("Sales Invoice", sale.name, "pharma_return_ledger", None, update_modified=False)
		return_chain_integrity.execute()
		ledger = json.loads(frappe.db.get_value("Sales Invoice", sale.name, "pharma_return_ledger"))
		self.assertEqual(sorted(ledger["returns"]), sorted([first.name, second.name]))
		self.assertEqual(ledger["returns"][second.name]["amounts"][item], 2000)
		# nothing else can be returned, the documents are untouched, the second run changes nothing
		with self.assertRaises(over_return_error()):
			post(doc(item, 1, batch, return_against=sale.name))
		self.assertEqual(frappe.db.get_value("Sales Invoice", second.name, "docstatus"), 1)
		before = frappe.db.get_value("Sales Invoice", sale.name, "pharma_return_ledger")
		logs = frappe.db.count("Error Log", {"method": "PharmacyOS: historical return chains"})
		return_chain_integrity.execute()
		self.assertEqual(frappe.db.get_value("Sales Invoice", sale.name, "pharma_return_ledger"), before)
		self.assertEqual(
			frappe.db.count("Error Log", {"method": "PharmacyOS: historical return chains"}), logs
		)
		# cancelling the historical chain return takes it out of the root's ledger
		second.reload()
		second.cancel()
		self.assertNotIn(
			second.name,
			json.loads(frappe.db.get_value("Sales Invoice", sale.name, "pharma_return_ledger"))["returns"],
		)


# --------------------------------------------------------------------------- D-2


class TestRefundBound(ReturnCase):
	def test_document_discount_on_the_sale(self):
		"""2 × 1000 with a 1000 discount: the customer paid 1000, a full-rate return refunded 2000."""
		item, batch = self.medicine(10)
		sale = post(doc(item, 2, batch, payment=1000, apply_discount_on="Grand Total", discount_amount=1000))
		self.assertEqual(flt(sale.grand_total), 1000)
		with self.assertRaises(RefundExceedsSaleError):
			post(doc(item, 2, batch, return_against=sale.name, payment=-2000))
		self.assertEqual(stock(item), (8, 8))
		# the legitimate refund (the discount carried over) goes through
		ret = post(
			doc(
				item,
				2,
				batch,
				return_against=sale.name,
				payment=-1000,
				apply_discount_on="Grand Total",
				discount_amount=-1000,
			)
		)
		self.assertEqual(flt(ret.grand_total), -1000)
		self.assertEqual(cash_credited(ret.name), 1000)
		self.assertEqual(stock(item), (10, 10))

	def test_row_discount_on_the_sale(self):
		"""2 × 1000 at −20 %: paid 1600; a return at the full rate must not refund 2000."""
		item, batch = self.medicine(10)
		d = doc(item, 2, batch, payment=1600)
		d["items"][0].update({"rate": 0, "price_list_rate": 1000, "discount_percentage": 20})
		sale = post(d)
		self.assertEqual(flt(sale.grand_total), 1600)
		# the full list rate is above the discounted rate charged (rate check); and if a client sends the
		# charged rate with the discount removed from the amount, the money check stops it
		with self.assertRaises((over_return_error(), RefundExceedsSaleError)):
			post(doc(item, 2, batch, return_against=sale.name, payment=-2000))
		with self.assertRaises(RefundExceedsSaleError):
			post(
				doc(
					item,
					2,
					batch,
					return_against=sale.name,
					rate=800,
					payment=-2000,
					apply_discount_on="Grand Total",
					discount_amount=400,
				)
			)
		self.assertEqual(stock(item), (8, 8))
		r = doc(item, 2, batch, return_against=sale.name, payment=-1600)
		r["items"][0].update({"rate": 0, "price_list_rate": 1000, "discount_percentage": 20})
		ret = post(r)
		self.assertEqual(cash_credited(ret.name), 1600)

	def test_tax_row_added_on_the_return(self):
		item, batch = self.medicine(10)
		sale = post(doc(item, 2, batch))
		tax_account = frappe.db.get_value(
			"Account", {"company": COMPANY, "account_type": "Tax", "is_group": 0}
		)
		taxes = [
			{"charge_type": "Actual", "account_head": tax_account, "description": "VAT", "tax_amount": -500}
		]
		with self.assertRaises(RefundExceedsSaleError):
			post(doc(item, 2, batch, return_against=sale.name, payment=-2500, taxes=taxes))
		self.assertEqual(stock(item), (8, 8))

	def test_document_discount_on_the_return(self):
		item, batch = self.medicine(10)
		sale = post(doc(item, 2, batch))
		with self.assertRaises(RefundExceedsSaleError):
			post(
				doc(
					item,
					2,
					batch,
					return_against=sale.name,
					payment=-2500,
					apply_discount_on="Grand Total",
					discount_amount=500,
				)
			)

	def test_alternative_uom_return_at_the_per_unit_rate(self):
		"""1 unit sold at 1000; a return of one 'strip' (0.1 unit) at rate 1000 would refund 1000."""
		unit = (
			frappe.get_doc("UOM", "R4 Fraction")
			if frappe.db.exists("UOM", "R4 Fraction")
			else frappe.get_doc(
				{"doctype": "UOM", "uom_name": "R4 Fraction", "must_be_whole_number": 0}
			).insert()
		)
		strip = (
			frappe.get_doc("UOM", "R4 Strip")
			if frappe.db.exists("UOM", "R4 Strip")
			else frappe.get_doc(
				{"doctype": "UOM", "uom_name": "R4 Strip", "must_be_whole_number": 1}
			).insert()
		)
		item, batch = self.medicine(10, stock_uom=unit.name)
		it = frappe.get_doc("Item", item)
		it.append("uoms", {"uom": strip.name, "conversion_factor": 0.1})
		it.save()
		sale = post(doc(item, 1, batch))
		r = doc(item, 1, batch, return_against=sale.name, payment=-1000)
		r["items"][0].update({"uom": strip.name, "conversion_factor": 0.1})
		with self.assertRaises(
			over_return_error()
		):  # the rate per stock unit (10 000) exceeds the 1000 charged
			post(r)
		self.assertEqual(stock(item), (9, 9))
		r = doc(item, 1, batch, return_against=sale.name, rate=100, payment=-100)
		r["items"][0].update({"uom": strip.name, "conversion_factor": 0.1})
		ret = post(r)  # 0.1 unit at 100 per strip = the per-unit rate: fine
		self.assertEqual(cash_credited(ret.name), 100)
		self.assertEqual(stock(item), (9.1, 9.1))

	def test_partial_return_gets_the_proportional_entitlement(self):
		"""3 × 1000 with a 300 discount (paid 2700): one unit is worth 900, not 1000."""
		item, batch = self.medicine(10)
		sale = post(doc(item, 3, batch, payment=2700, apply_discount_on="Grand Total", discount_amount=300))
		with self.assertRaises(RefundExceedsSaleError):
			post(doc(item, 1, batch, return_against=sale.name, payment=-1000))
		ret = post(
			doc(
				item,
				1,
				batch,
				return_against=sale.name,
				payment=-900,
				apply_discount_on="Grand Total",
				discount_amount=-100,
			)
		)
		self.assertEqual(cash_credited(ret.name), 900)
		# the rest, in two steps, then nothing more
		post(
			doc(
				item,
				1,
				batch,
				return_against=sale.name,
				payment=-900,
				apply_discount_on="Grand Total",
				discount_amount=-100,
			)
		)
		post(
			doc(
				item,
				1,
				batch,
				return_against=sale.name,
				payment=-900,
				apply_discount_on="Grand Total",
				discount_amount=-100,
			)
		)
		with self.assertRaises(over_return_error()):
			post(
				doc(
					item,
					1,
					batch,
					return_against=sale.name,
					payment=-900,
					apply_discount_on="Grand Total",
					discount_amount=-100,
				)
			)
		self.assertEqual(stock(item), (10, 10))
		returned = returned_so_far("Sales Invoice", sale.name)
		self.assertEqual(
			(flt(returned.qty[item]), flt(returned.amount[item], 2), flt(returned.total, 2)), (3, 2700, 2700)
		)

	def test_rounding_of_a_distributed_discount_is_tolerated_but_not_exploitable(self):
		"""3 × 1000 with a 1000 discount: 666.67 per unit; three partial returns pass, a fourth does not.

		Round 5: refunds are rounded cumulatively, so the three returns refund exactly the 2000 charged
		(667 + 666 + 667) instead of 2000.01."""
		item, batch = self.medicine(10)
		sale = post(doc(item, 3, batch, payment=2000, apply_discount_on="Grand Total", discount_amount=1000))
		for _ in range(3):
			post(doc(item, 1, batch, return_against=sale.name, rate=666.67, payment=-666.67))
		returned = returned_so_far("Sales Invoice", sale.name)
		self.assertEqual(flt(returned.amount[item], 2), 2000)
		self.assertEqual(flt(returned.total, 2), 2000)
		with self.assertRaises(over_return_error()):
			post(doc(item, 1, batch, return_against=sale.name, rate=666.67, payment=-666.67))

	def test_cancelling_a_return_restores_the_allowance(self):
		item, batch = self.medicine(10)
		sale = post(doc(item, 2, batch))
		first = post(doc(item, 2, batch, return_against=sale.name))
		with self.assertRaises(over_return_error()):
			post(doc(item, 1, batch, return_against=sale.name))
		first.cancel()
		ret = post(doc(item, 2, batch, return_against=sale.name))
		self.assertEqual(cash_credited(ret.name), 2000)
		self.assertEqual(stock(item), (10, 10))
		returned = returned_so_far("Sales Invoice", sale.name)
		self.assertEqual((flt(returned.qty[item]), flt(returned.amount[item])), (2, 2000))

	def test_erpnext_return_button_partial_return_still_works(self):
		try:
			from erpnext.accounts.doctype.sales_invoice.mapper import make_sales_return
		except ImportError:
			from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

		item, batch = self.medicine(10)
		sale = post(doc(item, 3, batch, payment=2700, apply_discount_on="Grand Total", discount_amount=300))
		ret = make_sales_return(sale.name)
		ret.items[0].qty = -1
		ret.calculate_taxes_and_totals()
		ret.payments[0].amount = ret.grand_total  # the cashier pays out the recomputed total
		ret.insert()
		ret.submit()
		self.assertLessEqual(-flt(ret.grand_total), 900.01)
		self.assertEqual(stock(item), (8, 8))


# --------------------------------------------------------------------------- references and precision


class TestReferences(ReturnCase):
	def test_draft_reference_is_refused_with_a_controlled_error(self):
		item, batch = self.medicine(10)
		draft = frappe.get_doc(doc(item, 2, batch)).insert()
		with self.assertRaises(ReturnAgainstCancelledSaleError):
			frappe.get_doc(doc(item, 2, batch, return_against=draft.name)).insert()
		self.assertEqual(stock(item), (10, 10))

	def test_fractional_quantities_at_the_stock_precision(self):
		unit = (
			frappe.get_doc("UOM", "R4 Fraction")
			if frappe.db.exists("UOM", "R4 Fraction")
			else frappe.get_doc(
				{"doctype": "UOM", "uom_name": "R4 Fraction", "must_be_whole_number": 0}
			).insert()
		)
		item, batch = self.medicine(10, stock_uom=unit.name)
		sale = post(doc(item, 1, batch))
		precision = frappe.get_precision("Sales Invoice Item", "stock_qty") or 3
		step = 10**-precision
		third = flt(1 / 3, precision)
		for _ in range(3):
			post(doc(item, third, batch, return_against=sale.name, payment=-flt(third * 1000, 2)))
		rest = flt(1 - 3 * third, precision)
		if rest:
			post(doc(item, rest, batch, return_against=sale.name, payment=-flt(rest * 1000, 2)))
		with self.assertRaises(over_return_error()):
			post(doc(item, step, batch, return_against=sale.name, payment=-flt(step * 1000, 2)))
		self.assertEqual(stock(item), (10, 10))
		self.assertEqual(flt(returned_so_far("Sales Invoice", sale.name).qty[item], precision), 1)


# --------------------------------------------------------------------------- is_consolidated


class TestConsolidatedFlag(ReturnCase):
	def test_client_supplied_is_consolidated_is_a_controlled_error(self):
		from pharmacyos_erp.pharmacy.consolidation import ConsolidatedFlagError, consolidating

		item, batch = self.medicine(10)
		for extra in ({"is_consolidated": 1}, {"is_consolidated": 1, "docstatus": 1}):
			with self.assertRaises(ConsolidatedFlagError):
				frappe.get_doc({**doc(item, 1, batch), **extra}).insert()
		self.assertEqual(stock(item), (10, 10))
		# inside POS consolidation the flag is ERPNext's own business
		with consolidating():
			self.assertTrue(frappe.flags.get("pharmacyos_pos_consolidation"))
		self.assertFalse(frappe.flags.get("pharmacyos_pos_consolidation"))

	def test_over_http_as_a_cashier(self):
		from frappe.tests.test_api import make_request
		from frappe.utils.password import update_password

		from pharmacyos_erp.tests.utils import make_user, profile_roles

		item, batch = self.medicine(10)
		cashier = make_user("r4-consolidated-cashier@example.com", profile_roles("Cashier"))
		update_password(cashier, "R4-pass-123456")
		frappe.defaults.set_user_default("Company", COMPANY, cashier)
		frappe.db.commit()
		client = get_test_client()
		make_request(client.post, ("/api/method/login",), {"json": {"usr": cashier, "pwd": "R4-pass-123456"}})
		response = make_request(
			client.post,
			("/api/method/frappe.client.insert",),
			{"json": {"doc": {**doc(item, 1, batch), "is_consolidated": 1, "docstatus": 1}}},
		)
		self.assertEqual(response.status_code, 417, response.get_data(as_text=True)[:300])
		self.assertIn("ConsolidatedFlagError", response.get_data(as_text=True))
