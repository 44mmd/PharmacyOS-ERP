# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""The PharmacyOS staff / API permission matrix, exercised through real HTTP requests.

Pure Python with no Frappe import, so the same definitions run

* in the test suite (`test_permissions_matrix.py`) through Frappe's request handler with real logins,
* against a running multi-worker server (`pharmacyos/dev/permission_check.py`) with API keys.

Every operation is a real REST / RPC call made *as that user* (`frappe.client.*`, the POS screen's
own endpoints, the PharmacyOS API). Test data is created by an administrator client, also over HTTP.

EXPECTED is the product's role model: "allow" must answer 200, "deny" must answer 403
(PermissionError). Anything else — a 5xx, or a 417 where a permission decision was expected — is a
failure, so a refused request can never pass for a broken one. Cells left out are not asserted.
"""

import uuid

ALLOW, DENY = "allow", "deny"

PROFILES = (
	"Pharmacy Owner",
	"Pharmacy Manager",
	"Cashier",
	"Pharmacist",
	"Pharmacy Accountant",
	"Inventory Manager",
	"PharmacyOS Integration",
	"Guest",
)


def _row(**cells):
	"""Expectation row; keys are short profile names."""
	names = {
		"owner": "Pharmacy Owner",
		"manager": "Pharmacy Manager",
		"cashier": "Cashier",
		"pharmacist": "Pharmacist",
		"finance": "Pharmacy Accountant",
		"inventory": "Inventory Manager",
		"integration": "PharmacyOS Integration",
		"guest": "Guest",
	}
	return {names[k]: v for k, v in cells.items()}


A, D = ALLOW, DENY
EXPECTED = {
	# counter sale: draft saved, then submitted (the POS screen's two calls)
	"sales_invoice.sell": _row(
		owner=A, manager=A, cashier=A, pharmacist=A, inventory=D, integration=D, guest=D
	),
	"sales_invoice.read": _row(
		owner=A, manager=A, cashier=A, pharmacist=A, finance=A, inventory=D, integration=D, guest=D
	),
	# cancelling a batch-medicine sale (stock, batch and GL reversal)
	"sales_invoice.cancel": _row(
		owner=A, manager=A, finance=A, cashier=D, pharmacist=D, inventory=D, integration=D, guest=D
	),
	"sales_invoice.return": _row(
		owner=A, manager=A, cashier=A, pharmacist=A, inventory=D, integration=D, guest=D
	),
	# credit notes beyond the counter return (round 3): free-standing (no original sale, here a
	# non-medicine item) and money-only (no goods back against a stock sale) need a manager
	"credit_note.standalone": _row(
		owner=A, manager=A, cashier=D, pharmacist=D, inventory=D, integration=D, guest=D
	),
	"credit_note.money_only": _row(
		owner=A, manager=A, cashier=D, pharmacist=D, inventory=D, integration=D, guest=D
	),
	# the whole shift as the POS screen runs it: open, list items, sell, close with the cash count
	"pos.full_shift": _row(owner=A, manager=A, cashier=A, pharmacist=A, integration=D, guest=D),
	"sales_order.create": _row(
		owner=A, manager=A, cashier=D, pharmacist=D, finance=D, inventory=D, integration=D, guest=D
	),
	"sales_order.submit": _row(
		owner=A, manager=A, cashier=D, pharmacist=D, finance=D, inventory=D, integration=D, guest=D
	),
	"sales_order.cancel": _row(
		owner=A, manager=A, cashier=D, pharmacist=D, finance=D, inventory=D, integration=D, guest=D
	),
	"delivery_note.create": _row(
		owner=A, manager=A, inventory=A, cashier=D, pharmacist=D, finance=D, integration=D, guest=D
	),
	"delivery_note.submit": _row(
		owner=A, manager=A, inventory=A, cashier=D, pharmacist=D, finance=D, integration=D, guest=D
	),
	"delivery_note.cancel": _row(
		owner=A, manager=A, inventory=A, cashier=D, pharmacist=D, finance=D, integration=D, guest=D
	),
	"stock_reservation.create": _row(cashier=D, pharmacist=D, finance=D, integration=D, guest=D),
	"journal_entry.create": _row(
		owner=A, manager=A, finance=A, cashier=D, pharmacist=D, inventory=D, integration=D, guest=D
	),
	"payment_entry.create": _row(
		owner=A, manager=A, finance=A, cashier=D, pharmacist=D, inventory=D, integration=D, guest=D
	),
	"stock_entry.create": _row(
		owner=A, manager=A, inventory=A, cashier=D, pharmacist=D, finance=D, integration=D, guest=D
	),
	"gl_entry.read": _row(
		owner=A, manager=A, finance=A, cashier=D, pharmacist=D, inventory=D, integration=D, guest=D
	),
	"item_price.read": _row(
		owner=A, manager=A, cashier=A, pharmacist=A, finance=A, inventory=A, integration=D, guest=D
	),
	"item_price.create": _row(
		owner=A, manager=A, inventory=A, cashier=D, pharmacist=D, finance=D, integration=D, guest=D
	),
	"item_price.write": _row(
		owner=A, manager=A, inventory=A, cashier=D, pharmacist=D, finance=D, integration=D, guest=D
	),
	"item_price.delete": _row(
		owner=A, manager=A, inventory=D, cashier=D, pharmacist=D, finance=D, integration=D, guest=D
	),
	# "Add Medicine" with a selling price: ERPNext creates the Item Price as the calling user
	"medicine.create_with_price": _row(
		owner=A, manager=A, inventory=A, cashier=D, pharmacist=D, finance=D, integration=D, guest=D
	),
	"batch.read": _row(owner=A, manager=A, pharmacist=A, inventory=A, cashier=D, integration=D, guest=D),
	"stock_ledger.read": _row(
		owner=A, manager=A, pharmacist=A, inventory=A, cashier=D, integration=D, guest=D
	),
	"branch.read": _row(
		owner=A, manager=A, cashier=A, pharmacist=A, finance=A, inventory=A, integration=D, guest=D
	),
	# the Cloud's only door into the ERP; staff credentials are refused
	"integration.create_order": _row(integration=A, owner=D, manager=D, cashier=D, pharmacist=D, guest=D),
	"integration.availability": _row(integration=A, cashier=D, pharmacist=D, guest=D),
}


def _uid():
	return uuid.uuid4().hex[:8].upper()


class Matrix:
	"""Runs EXPECTED. `clients` maps profile → transport; `admin` prepares data.

	A transport has `post(method, data) -> (status, body)` and `get(method, params) -> (status, body)`;
	`ctx` describes the site: company, warehouse, customer, currency, cash_account, price_list,
	branch_code, and optional `sale_extra` / `row_extra` field defaults (e.g. test-company accounts).
	"""

	def __init__(self, admin, clients, ctx, pos_profiles=None):
		"""`pos_profiles`: profile → (POS Profile name, user) — one counter of their own per seller."""
		self.admin, self.clients, self.ctx = admin, clients, ctx
		self.pos_profiles = pos_profiles or {}
		self.results = []

	# ------------------------------------------------------------------ admin helpers

	def ok(self, method, **data):
		status, body = self.admin.post(method, data)
		if status != 200:
			raise RuntimeError(f"admin {method}: {status} {str(body)[:500]}")
		return body.get("message")

	def medicine(self, stock=20, publish=False):
		code = "PM-" + _uid()
		self.ok(
			"pharmacyos_erp.pharmacy.medicine.create_medicine",
			item_code=code,
			item_name=f"Matrix {code}",
			item_group=self.ctx.get("item_group", "Products"),
			stock_uom="Nos",
			standard_rate=1000,
		)
		batch = self.ok(
			"pharmacyos_erp.pharmacy.receiving.create_receiving_batch",
			item_code=code,
			batch_id=f"PMB-{code}",
			expiry_date="2099-12-31",
		)["name"]
		if publish:
			self.ok(
				"frappe.client.set_value", doctype="Item", name=code, fieldname="pharmacyos_publish", value=1
			)
		if stock:
			self.ok("frappe.client.insert", doc=self.stock_entry(code, batch, stock, docstatus=1))
		return code, batch

	def stock_entry(self, code, batch, qty, docstatus=0):
		return {
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": self.ctx["company"],
			"docstatus": docstatus,
			"items": [
				{
					"item_code": code,
					"qty": qty,
					"t_warehouse": self.ctx["warehouse"],
					"basic_rate": 500,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
					**self.ctx.get("stock_row_extra", {}),
				}
			],
		}

	def sale_doc(self, code, batch, qty, return_against=None, docstatus=0, pos_profile=None):
		sign = -1 if return_against else 1
		doc = {
			"doctype": "Sales Invoice",
			"company": self.ctx["company"],
			"customer": self.ctx["customer"],
			"currency": self.ctx["currency"],
			"is_pos": 1,
			"update_stock": 1,
			"is_return": 1 if return_against else 0,
			"return_against": return_against,
			"docstatus": docstatus,
			"items": [
				{
					"item_code": code,
					"qty": sign * qty,
					"rate": 1000,
					"warehouse": self.ctx["warehouse"],
					"batch_no": batch,
					"use_serial_batch_fields": 1,
					**self.ctx.get("row_extra", {}),
				}
			],
			"payments": [
				{"mode_of_payment": "Cash", "account": self.ctx["cash_account"], "amount": sign * qty * 1000}
			],
			**self.ctx.get("sale_extra", {}),
		}
		if pos_profile:
			doc.update({"pos_profile": pos_profile, "is_created_using_pos": 1})
		return doc

	def order_doc(self, code, docstatus=0):
		return {
			"doctype": "Sales Order",
			"company": self.ctx["company"],
			"customer": self.ctx["customer"],
			"currency": self.ctx["currency"],
			"selling_price_list": self.ctx["price_list"],
			"delivery_date": "2099-01-01",
			"docstatus": docstatus,
			"items": [
				{
					"item_code": code,
					"qty": 1,
					"rate": 1000,
					"warehouse": self.ctx["warehouse"],
					"delivery_date": "2099-01-01",
				}
			],
		}

	def delivery_doc(self, code, batch, docstatus=0):
		return {
			"doctype": "Delivery Note",
			"company": self.ctx["company"],
			"customer": self.ctx["customer"],
			"currency": self.ctx["currency"],
			"selling_price_list": self.ctx["price_list"],
			"docstatus": docstatus,
			"items": [
				{
					"item_code": code,
					"qty": 1,
					"rate": 1000,
					"warehouse": self.ctx["warehouse"],
					"batch_no": batch,
					"use_serial_batch_fields": 1,
					**self.ctx.get("dn_row_extra", {}),
				}
			],
		}

	# ------------------------------------------------------------------ operations (as `c`)

	@staticmethod
	def _chain(*calls):
		"""Run dependent calls; the first non-200 decides."""
		result = None
		for call in calls:
			result = call(result)
			if result[0] != 200:
				return result
		return result

	def own_shift(self, c, profile):
		"""The seller's own counter with their shift open (counter staff sell and refund only in their own
		open shift); None for profiles without a counter. Opening is tried as that user (it may be refused)."""
		counter = self.pos_profiles.get(profile)
		if not counter:
			return None
		status, body = c.post("pharmacyos_erp.pos.api.get_context", {})
		if status == 200 and not body["message"].get("shift"):
			c.post(
				"erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher",
				{"pos_profile": counter[0], "company": self.ctx["company"], "balance_details": [{"mode_of_payment": "Cash", "opening_amount": 0}]},
			)
		return counter[0]

	def op_sales_invoice_sell(self, c, profile):
		code, batch = self.medicine()
		pos_profile = self.own_shift(c, profile)
		return self._chain(
			lambda _r: c.post("frappe.client.insert", {"doc": self.sale_doc(code, batch, 1, pos_profile=pos_profile)}),
			lambda r: c.post("frappe.client.submit", {"doc": r[1]["message"]}),
		)

	def op_sales_invoice_read(self, c, profile):
		return c.post("frappe.client.get_list", {"doctype": "Sales Invoice", "limit_page_length": 5})

	def op_sales_invoice_cancel(self, c, profile):
		code, batch = self.medicine(stock=5)
		sale = self.ok("frappe.client.insert", doc=self.sale_doc(code, batch, 2, docstatus=1))["name"]
		status, body = c.post("frappe.client.cancel", {"doctype": "Sales Invoice", "name": sale})
		if status == 200:
			self.verify_cancel(sale, code, batch)
		return status, body

	def op_sales_invoice_return(self, c, profile):
		code, batch = self.medicine(stock=5)
		sale = self.ok("frappe.client.insert", doc=self.sale_doc(code, batch, 2, docstatus=1))["name"]
		pos_profile = self.own_shift(c, profile)
		return self._chain(
			lambda _r: c.post(
				"frappe.client.insert", {"doc": self.sale_doc(code, batch, 1, return_against=sale, pos_profile=pos_profile)}
			),
			lambda r: c.post("frappe.client.submit", {"doc": r[1]["message"]}),
		)

	def non_medicine(self, stock=5):
		code = "PNM-" + _uid()
		self.ok(
			"frappe.client.insert",
			doc={
				"doctype": "Item",
				"item_code": code,
				"item_name": f"Matrix product {code}",
				"item_group": self.ctx.get("item_group", "Products"),
				"stock_uom": "Nos",
				"is_stock_item": 1,
			},
		)
		entry = self.stock_entry(code, None, stock, docstatus=1)
		for row in entry["items"]:
			row.pop("batch_no"), row.pop("use_serial_batch_fields")
		self.ok("frappe.client.insert", doc=entry)
		return code

	def _plain_sale(self, code, qty, return_against=None, update_stock=1, docstatus=0):
		doc = self.sale_doc(code, None, qty, return_against=return_against, docstatus=docstatus)
		doc["update_stock"] = update_stock
		for row in doc["items"]:
			row.pop("batch_no"), row.pop("use_serial_batch_fields")
		return doc

	def op_credit_note_standalone(self, c, profile):
		code = self.non_medicine()
		doc = self._plain_sale(code, 1)
		doc.update({"is_return": 1, "docstatus": 1})
		doc["items"][0]["qty"] = -1
		doc["payments"][0]["amount"] = -1000
		return c.post("frappe.client.insert", {"doc": doc})

	def op_credit_note_money_only(self, c, profile):
		code = self.non_medicine()
		sale = self.ok("frappe.client.insert", doc=self._plain_sale(code, 2, docstatus=1))["name"]
		return c.post(
			"frappe.client.insert",
			{"doc": self._plain_sale(code, 1, return_against=sale, update_stock=0, docstatus=1)},
		)

	def op_pos_full_shift(self, c, profile):
		counter = self.pos_profiles.get(profile)
		# roles without a counter of their own try someone else's: the server must refuse
		pos_profile, user = counter or next(iter(self.pos_profiles.values()), (None, None))
		if not pos_profile:
			return 599, {"exc_type": "SetupError", "note": "no POS profile in the matrix context"}
		base = "erpnext.selling.page.point_of_sale.point_of_sale."
		state = {}

		def opened(r):
			state["opening"] = r[1]["message"]
			return r

		def sold(r):
			state["sale"] = r[1]["message"]
			return r

		def closing(r):
			data = r[1]["message"]
			opening = state["opening"]
			doc = {
				"doctype": "POS Closing Entry",
				"pos_opening_entry": opening["name"],
				"period_start_date": opening["period_start_date"],
				"period_end_date": data["end"],
				"pos_profile": pos_profile,
				"user": user,
				"company": self.ctx["company"],
				"sales_invoices": [
					{
						"sales_invoice": i["name"],
						"posting_date": i["posting_date"],
						"grand_total": i["grand_total"],
						"customer": i["customer"],
						"is_return": i["is_return"],
						"return_against": i.get("return_against"),
					}
					for i in data["invoices"]
				],
				"payment_reconciliation": [
					{
						"mode_of_payment": p["mode_of_payment"],
						"opening_amount": 0,
						"expected_amount": p["amount"],
						"closing_amount": p["amount"],
					}
					for p in data["payments"]
				],
			}
			if not any(i["sales_invoice"] == state["sale"]["name"] for i in doc["sales_invoices"]):
				return 599, {
					"exc_type": "AssertionError",
					"note": "the shift's sale is missing from the closing",
				}
			return c.post("frappe.client.insert", {"doc": doc})

		code, batch = self.medicine(stock=5)
		result = self._chain(
			lambda _r: c.post(base + "get_pos_profile_data", {"pos_profile": pos_profile}),
			lambda _r: c.post(
				base + "get_items",
				{
					"start": 0,
					"page_length": 20,
					"price_list": self.ctx["price_list"],
					"item_group": self.ctx.get("all_item_groups", "All Item Groups"),
					"pos_profile": pos_profile,
					"search_term": code,
				},
			),
			lambda _r: opened(self._open_or_current(c, base, pos_profile)),
			lambda _r: c.post(
				"frappe.client.insert", {"doc": self.sale_doc(code, batch, 1, pos_profile=pos_profile)}
			),
			lambda r: sold(c.post("frappe.client.submit", {"doc": r[1]["message"]})),
			lambda _r: self._with_end(
				c.post(
					"erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry.get_invoices",
					{
						"start": state["opening"]["period_start_date"],
						"end": _after(state["sale"]),
						"pos_profile": pos_profile,
						"user": user,
					},
				),
				_after(state["sale"]),
			),
			closing,
			lambda r: c.post("frappe.client.submit", {"doc": r[1]["message"]}),
		)
		return result

	def _open_or_current(self, c, base, pos_profile):
		"""Open the shift — or take the user's shift already open on this counter (another operation of
		the matrix may have opened it: counter staff sell only in their own open shift)."""
		status, body = c.post("pharmacyos_erp.pos.api.get_context", {})
		shift = status == 200 and body["message"].get("shift")
		if shift and shift.get("pos_profile") == pos_profile:
			return 200, {"message": shift}
		return c.post(
			base + "create_opening_voucher",
			{
				"pos_profile": pos_profile,
				"company": self.ctx["company"],
				"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 0}],
			},
		)

	@staticmethod
	def _with_end(result, end):
		status, body = result
		if status == 200:
			body["message"]["end"] = end
		return status, body

	def op_sales_order_create(self, c, profile):
		code, _batch = self.medicine(stock=5)
		return c.post("frappe.client.insert", {"doc": self.order_doc(code)})

	def op_sales_order_submit(self, c, profile):
		code, _batch = self.medicine(stock=5)
		return self._chain(
			lambda _r: c.post("frappe.client.insert", {"doc": self.order_doc(code)}),
			lambda r: c.post("frappe.client.submit", {"doc": r[1]["message"]}),
		)

	def op_sales_order_cancel(self, c, profile):
		code, _batch = self.medicine(stock=5)
		so = self.ok("frappe.client.insert", doc=self.order_doc(code, docstatus=1))["name"]
		return c.post("frappe.client.cancel", {"doctype": "Sales Order", "name": so})

	def op_delivery_note_create(self, c, profile):
		code, batch = self.medicine(stock=5)
		return c.post("frappe.client.insert", {"doc": self.delivery_doc(code, batch)})

	def op_delivery_note_submit(self, c, profile):
		code, batch = self.medicine(stock=5)
		return self._chain(
			lambda _r: c.post("frappe.client.insert", {"doc": self.delivery_doc(code, batch)}),
			lambda r: c.post("frappe.client.submit", {"doc": r[1]["message"]}),
		)

	def op_delivery_note_cancel(self, c, profile):
		code, batch = self.medicine(stock=5)
		dn = self.ok("frappe.client.insert", doc=self.delivery_doc(code, batch, docstatus=1))["name"]
		return c.post("frappe.client.cancel", {"doctype": "Delivery Note", "name": dn})

	def op_stock_reservation_create(self, c, profile):
		code, _batch = self.medicine(stock=5)
		so = self.ok("frappe.client.insert", doc=self.order_doc(code, docstatus=1))
		return c.post(
			"frappe.client.insert",
			{
				"doc": {
					"doctype": "Stock Reservation Entry",
					"item_code": code,
					"warehouse": self.ctx["warehouse"],
					"voucher_type": "Sales Order",
					"voucher_no": so["name"],
					"voucher_detail_no": so["items"][0]["name"],
					"available_qty": 5,
					"voucher_qty": 1,
					"stock_uom": "Nos",
					"reserved_qty": 1,
					"company": self.ctx["company"],
				}
			},
		)

	def op_journal_entry_create(self, c, profile):
		return c.post(
			"frappe.client.insert",
			{
				"doc": {
					"doctype": "Journal Entry",
					"voucher_type": "Journal Entry",
					"company": self.ctx["company"],
					"posting_date": self.ctx["today"],
					"accounts": [
						{
							"account": self.ctx["cash_account"],
							"debit_in_account_currency": 777,
							**self.ctx.get("je_row_extra", {}),
						},
						{
							"account": self.ctx["income_account"],
							"credit_in_account_currency": 777,
							**self.ctx.get("je_row_extra", {}),
						},
					],
				}
			},
		)

	def op_payment_entry_create(self, c, profile):
		return c.post(
			"frappe.client.insert",
			{
				"doc": {
					"doctype": "Payment Entry",
					"payment_type": "Receive",
					"company": self.ctx["company"],
					"party_type": "Customer",
					"party": self.ctx["customer"],
					"paid_from": self.ctx["receivable_account"],
					"paid_to": self.ctx["cash_account"],
					"paid_amount": 500,
					"received_amount": 500,
					"reference_no": "matrix",
					"reference_date": self.ctx["today"],
				}
			},
		)

	def op_stock_entry_create(self, c, profile):
		code, batch = self.medicine(stock=0)
		return c.post("frappe.client.insert", {"doc": self.stock_entry(code, batch, 100)})

	def op_gl_entry_read(self, c, profile):
		return c.post("frappe.client.get_list", {"doctype": "GL Entry", "limit_page_length": 5})

	def _price(self):
		code, _batch = self.medicine(stock=0)
		return code, self.ok(
			"frappe.client.get_value",
			doctype="Item Price",
			filters={"item_code": code, "price_list": self.ctx["price_list"]},
			fieldname="name",
		)["name"]

	def op_item_price_read(self, c, profile):
		_code, name = self._price()
		return c.post("frappe.client.get", {"doctype": "Item Price", "name": name})

	def op_item_price_create(self, c, profile):
		code, _batch = self.medicine(stock=0)
		return c.post(
			"frappe.client.insert",
			{
				"doc": {
					"doctype": "Item Price",
					"item_code": code,
					"price_list": self.ctx["buying_price_list"],
					"price_list_rate": 650,
				}
			},
		)

	def op_item_price_write(self, c, profile):
		_code, name = self._price()
		status, body = c.post(
			"frappe.client.set_value",
			{"doctype": "Item Price", "name": name, "fieldname": "price_list_rate", "value": 1250},
		)
		if status == 200 and float(body["message"]["price_list_rate"]) != 1250:
			return 500, {"exc_type": "AssertionError", "note": "price not changed"}
		return status, body

	def op_item_price_delete(self, c, profile):
		_code, name = self._price()
		return c.post("frappe.client.delete", {"doctype": "Item Price", "name": name})

	def op_medicine_create_with_price(self, c, profile):
		code = "PMN-" + _uid()
		return c.post(
			"pharmacyos_erp.pharmacy.medicine.create_medicine",
			{
				"item_code": code,
				"item_name": f"Matrix new {code}",
				"item_group": self.ctx.get("item_group", "Products"),
				"stock_uom": "Nos",
				"standard_rate": 1750,
			},
		)

	def op_batch_read(self, c, profile):
		# opening a batch record (cashiers only *select* a batch on the invoice line)
		_code, batch = self.medicine(stock=0)
		return c.post("frappe.client.get", {"doctype": "Batch", "name": batch})

	def op_stock_ledger_read(self, c, profile):
		return c.post("frappe.client.get_list", {"doctype": "Stock Ledger Entry", "limit_page_length": 5})

	def op_branch_read(self, c, profile):
		return c.post("frappe.client.get_list", {"doctype": "Branch", "limit_page_length": 5})

	def op_integration_create_order(self, c, profile):
		code, _batch = self.medicine(stock=5, publish=True)
		return c.post(
			"pharmacyos_erp.api.v1.orders.create_order",
			{
				"order_id": f"MATRIX-{_uid()}",
				"branch_code": self.ctx["branch_code"],
				"items": [{"item_code": code, "qty": 1}],
				"customer": {"id": f"matrix-{_uid()}", "name": "Matrix"},
			},
		)

	def op_integration_availability(self, c, profile):
		return c.get(
			"pharmacyos_erp.api.v1.availability.get_availability",
			{"branch_code": self.ctx["branch_code"], "item_codes": "X"},
		)

	# ------------------------------------------------------------------ cancellation proof

	def verify_cancel(self, sale, code, batch):
		"""After a cancel: stock back where it was, GL reversed to zero, batch quantity restored."""
		self.cancel_checks = getattr(self, "cancel_checks", [])
		sle = self.ok(
			"frappe.client.get_list",
			doctype="Stock Ledger Entry",
			filters={"item_code": code, "warehouse": self.ctx["warehouse"], "is_cancelled": 0},
			fields=["actual_qty"],
			limit_page_length=0,
		)
		gl = self.ok(
			"frappe.client.get_list",
			doctype="GL Entry",
			filters={"voucher_no": sale},
			fields=["debit", "credit", "is_cancelled"],
			limit_page_length=0,
		)
		batch_qty = self.ok(
			"erpnext.stock.doctype.batch.batch.get_batch_qty", batch_no=batch, warehouse=self.ctx["warehouse"]
		)
		problems = []
		if sum(r["actual_qty"] for r in sle) != 5:
			problems.append(f"stock {sum(r['actual_qty'] for r in sle)} != 5 after cancel")
		if any(not g["is_cancelled"] for g in gl):
			problems.append("GL entries of the cancelled sale still active")
		if round(sum(g["debit"] for g in gl) - sum(g["credit"] for g in gl), 2) != 0:
			problems.append("GL not balanced")
		if float(batch_qty or 0) != 5:
			problems.append(f"batch qty {batch_qty} != 5")
		self.cancel_checks.append((sale, problems))

	# ------------------------------------------------------------------ runner

	def run(self, only=None):
		for op, expectations in EXPECTED.items():
			if only and op not in only:
				continue
			method = getattr(self, "op_" + op.replace(".", "_"))
			for profile, expected in expectations.items():
				client = self.clients.get(profile)
				if client is None:
					continue
				try:
					status, body = method(client, profile)
				except Exception as e:  # a crash in the probe itself is a failure, never a pass
					status, body = 599, {"exc_type": type(e).__name__, "detail": str(e)[:300]}
				observed = ALLOW if status == 200 else DENY if status == 403 else f"error {status}"
				exc = (body or {}).get("exc_type") if isinstance(body, dict) else None
				self.results.append(
					{
						"op": op,
						"profile": profile,
						"expected": expected,
						"observed": observed,
						"ok": observed == expected,
						"exc": exc,
						"detail": "" if observed == expected else _brief(body),
					}
				)
		for sale, problems in getattr(self, "cancel_checks", []):
			self.results.append(
				{
					"op": "sales_invoice.cancel.reversal",
					"profile": sale,
					"expected": "consistent",
					"observed": "consistent" if not problems else "; ".join(problems),
					"ok": not problems,
					"exc": None,
					"detail": "",
				}
			)
		return self.results

	def failures(self):
		return [r for r in self.results if not r["ok"]]

	def table(self):
		ops = list(dict.fromkeys(r["op"] for r in self.results if r["op"] in EXPECTED))
		lines = ["| operation | " + " | ".join(PROFILES) + " |", "|---|" + "---|" * len(PROFILES)]
		cells = {(r["op"], r["profile"]): r for r in self.results}
		for op in ops:
			row = []
			for profile in PROFILES:
				r = cells.get((op, profile))
				if not r:
					row.append("–")
				else:
					mark = "✓" if r["observed"] == ALLOW else "✗" if r["observed"] == DENY else r["observed"]
					row.append(mark if r["ok"] else f"**{mark} (expected {r['expected']})**")
			lines.append(f"| {op} | " + " | ".join(row) + " |")
		return "\n".join(lines)


def _after(doc, seconds=60):
	"""A server-clock timestamp just after a document's posting time (closing period end)."""
	import datetime

	posted = datetime.datetime.fromisoformat(
		f"{doc['posting_date']} {str(doc['posting_time']).split('.')[0]}"
	)
	return (posted + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _brief(body):
	if not isinstance(body, dict):
		return str(body)[:300]
	for key in ("exception", "detail", "note", "_server_messages"):
		if body.get(key):
			return str(body[key])[:300]
	return str(body)[:300]
