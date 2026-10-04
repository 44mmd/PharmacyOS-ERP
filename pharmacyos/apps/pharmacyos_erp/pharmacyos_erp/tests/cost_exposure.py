# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Red-team probe: can counter staff read cost data? (purchase prices, valuation, stock value)

Pure Python with no Frappe import (like `permission_matrix.py`), so it runs in the suite through
Frappe's request handler (`test_cost_exposure.py`) and against a live multi-worker server
(`pharmacyos/dev/permission_check.py --cost`).

An administrator receives a fresh medicine at a distinctive cost, gives it a distinctive buying price
and sells one unit. Every probe is then a real REST / RPC request made *as the user* — the document
APIs, list/report views, single values, desk form loads, the whitelisted ERPNext helpers that compute
costs, query reports — and the response is searched for the cost **values** (not field names), so a
leak through any field, alias or nested child row is found. A refused request (403) or an answer
without the values passes; any response containing them is a leak.

Cost-reading profiles (owner, manager, …) are probed too, as a control: the probe must see the values
there, otherwise it would prove nothing.
"""

import json
import uuid

# distinctive numbers: valuation (incoming cost per unit), the Item's own valuation rate, the last
# purchase rate and the buying price; stock values derive from the first
VALUATION = 7319.17
ITEM_VALUATION = 8137.19
LAST_PURCHASE = 6421.33
BUYING = 5213.77
RECEIVED = 10


def _uid():
	return uuid.uuid4().hex[:8].upper()


def secrets_for(qty_on_hand):
	"""String forms of every cost figure the data set produces."""
	values = {
		VALUATION,
		ITEM_VALUATION,
		LAST_PURCHASE,
		BUYING,
		round(VALUATION * RECEIVED, 2),
		round(VALUATION * qty_on_hand, 2),
	}
	out = set()
	for v in values:
		out.add(f"{v:.2f}".rstrip("0").rstrip("."))
	return out


class CostProbe:
	"""`admin` and `clients[profile]` are transports: post(method, data) / get(method, params) → (status, body)."""

	def __init__(self, admin, clients, ctx):
		self.admin, self.clients, self.ctx = admin, clients, ctx
		self.results = []  # (profile, probe, status, leaked values)

	def ok(self, method, **data):
		status, body = self.admin.post(method, data)
		if status != 200:
			raise RuntimeError(f"admin {method}: {status} {str(body)[:500]}")
		return body.get("message")

	# ------------------------------------------------------------------ data

	def prepare(self):
		ctx = self.ctx
		code = "COST-" + _uid()
		self.ok(
			"pharmacyos_erp.pharmacy.medicine.create_medicine",
			item_code=code,
			item_name=f"Cost probe {code}",
			item_group=ctx.get("item_group", "Products"),
			stock_uom="Nos",
			standard_rate=9900,
		)
		batch = self.ok(
			"pharmacyos_erp.pharmacy.receiving.create_receiving_batch",
			item_code=code,
			batch_id=f"CB-{code}",
			expiry_date="2099-12-31",
		)["name"]
		self.ok(
			"frappe.client.insert",
			doc={
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": ctx["company"],
				"docstatus": 1,
				"items": [
					{
						"item_code": code,
						"qty": RECEIVED,
						"t_warehouse": ctx["warehouse"],
						"basic_rate": VALUATION,
						"batch_no": batch,
						"use_serial_batch_fields": 1,
						**ctx.get("stock_row_extra", {}),
					}
				],
			},
		)
		buying = self.ok(
			"frappe.client.insert",
			doc={
				"doctype": "Item Price",
				"item_code": code,
				"price_list": ctx["buying_price_list"],
				"price_list_rate": BUYING,
			},
		)["name"]
		self.ok(
			"frappe.client.set_value",
			doctype="Item",
			name=code,
			fieldname={"last_purchase_rate": LAST_PURCHASE, "valuation_rate": ITEM_VALUATION},
		)
		sale = self.ok(
			"frappe.client.insert",
			doc={
				"doctype": "Sales Invoice",
				"company": ctx["company"],
				"customer": ctx["customer"],
				"currency": ctx["currency"],
				"is_pos": 1,
				"update_stock": 1,
				"items": [
					{
						"item_code": code,
						"qty": 1,
						"rate": 9900,
						"warehouse": ctx["warehouse"],
						"batch_no": batch,
						"use_serial_batch_fields": 1,
						**ctx.get("row_extra", {}),
					}
				],
				"payments": [{"mode_of_payment": "Cash", "account": ctx["cash_account"], "amount": 9900}],
				"docstatus": 1,
				**ctx.get("sale_extra", {}),
			},
		)
		bundle = (sale["items"][0].get("serial_and_batch_bundle")) or ""
		bin_name = self.ok(
			"frappe.client.get_value",
			doctype="Bin",
			filters={"item_code": code, "warehouse": ctx["warehouse"]},
			fieldname="name",
		)["name"]
		self.data = {
			"code": code,
			"batch": batch,
			"buying": buying,
			"sale": sale["name"],
			"bundle": bundle,
			"bin": bin_name,
		}
		self.secrets = secrets_for(RECEIVED - 1)
		return self.data

	# ------------------------------------------------------------------ probes

	def probes(self):
		d, ctx = self.data, self.ctx
		code, wh, company = d["code"], ctx["warehouse"], ctx["company"]
		yield "item.get", "post", "frappe.client.get", {"doctype": "Item", "name": code}
		# round 4: the Item DocType's own helper returns the whole record (`as_dict`), and the write
		# endpoints echo the saved document without field-level permissions
		yield (
			"item.doctype_helper",
			"post",
			"erpnext.stock.doctype.item.item.get_item_details",
			{"item_code": code, "company": company},
		)
		yield "sale.insert_echo", "insert_draft", None, self.sale_doc(code)
		yield "sale.save_echo", "save_draft", None, {}
		yield "sale.submit_echo", "submit_draft", None, {}
		yield "sale.rest_v1_post_echo", "rest_post", "Sales Invoice", self.sale_doc(code)
		yield "sale.rest_v2_create_echo", "rest_v2_post", "document/Sales Invoice", self.sale_doc(code)
		yield (
			"sale.versions",
			"post",
			"frappe.client.get_list",
			{
				"doctype": "Version",
				"filters": {"ref_doctype": "Sales Invoice", "docname": d["sale"]},
				"fields": ["name", "data"],
			},
		)
		yield "item.form_load", "get", "frappe.desk.form.load.getdoc", {"doctype": "Item", "name": code}
		yield (
			"item.list_fields",
			"post",
			"frappe.client.get_list",
			{
				"doctype": "Item",
				"filters": {"name": code},
				"fields": ["name", "valuation_rate", "last_purchase_rate"],
			},
		)
		yield (
			"item.list_star",
			"post",
			"frappe.client.get_list",
			{"doctype": "Item", "filters": {"name": code}, "fields": ["*"]},
		)
		yield (
			"item.get_value",
			"post",
			"frappe.client.get_value",
			{"doctype": "Item", "filters": code, "fieldname": ["valuation_rate", "last_purchase_rate"]},
		)
		yield (
			"item.reportview",
			"post",
			"frappe.desk.reportview.get",
			{
				"doctype": "Item",
				"filters": [["name", "=", code]],
				"fields": ["`tabItem`.`valuation_rate`", "`tabItem`.`last_purchase_rate`"],
			},
		)
		yield (
			"bin.list",
			"post",
			"frappe.client.get_list",
			{"doctype": "Bin", "filters": {"item_code": code}, "fields": ["*"]},
		)
		yield "bin.get", "post", "frappe.client.get", {"doctype": "Bin", "name": d["bin"]}
		yield (
			"bin.get_value",
			"post",
			"frappe.client.get_value",
			{"doctype": "Bin", "filters": d["bin"], "fieldname": ["stock_value", "valuation_rate"]},
		)
		yield (
			"bin.reportview_sum",
			"post",
			"frappe.desk.reportview.get",
			{
				"doctype": "Bin",
				"filters": [["item_code", "=", code]],
				"fields": [{"SUM": "stock_value", "as": "total"}],
			},
		)
		yield (
			"item_price.buying_list",
			"post",
			"frappe.client.get_list",
			{
				"doctype": "Item Price",
				"filters": {"item_code": code},
				"fields": ["name", "price_list", "price_list_rate", "buying"],
			},
		)
		yield (
			"item_price.buying_get",
			"post",
			"frappe.client.get",
			{"doctype": "Item Price", "name": d["buying"]},
		)
		yield (
			"item_price.buying_value",
			"post",
			"frappe.client.get_value",
			{
				"doctype": "Item Price",
				"filters": {"item_code": code, "buying": 1},
				"fieldname": "price_list_rate",
			},
		)
		yield (
			"sle.list",
			"post",
			"frappe.client.get_list",
			{"doctype": "Stock Ledger Entry", "filters": {"item_code": code}, "fields": ["*"]},
		)
		yield (
			"bundle.get",
			"post",
			"frappe.client.get",
			{"doctype": "Serial and Batch Bundle", "name": d["bundle"]},
		)
		yield (
			"bundle.entries",
			"post",
			"frappe.client.get_list",
			{
				"doctype": "Serial and Batch Bundle",
				"filters": {"item_code": code},
				"fields": ["name", "avg_rate", "total_amount"],
			},
		)
		yield "sale.get", "post", "frappe.client.get", {"doctype": "Sales Invoice", "name": d["sale"]}
		yield (
			"sale.form_load",
			"get",
			"frappe.desk.form.load.getdoc",
			{"doctype": "Sales Invoice", "name": d["sale"]},
		)
		yield (
			"sale.item_list",
			"post",
			"frappe.client.get_list",
			{
				"doctype": "Sales Invoice",
				"filters": {"name": d["sale"]},
				"fields": ["`tabSales Invoice Item`.`incoming_rate`"],
			},
		)
		yield (
			"erpnext.get_valuation_rate",
			"post",
			"erpnext.stock.get_item_details.get_valuation_rate",
			{"item_code": code, "company": company, "warehouse": wh},
		)
		yield (
			"erpnext.get_stock_balance",
			"post",
			"erpnext.stock.utils.get_stock_balance",
			{"item_code": code, "warehouse": wh, "with_valuation_rate": 1},
		)
		yield (
			"erpnext.get_incoming_rate",
			"post",
			"erpnext.stock.utils.get_incoming_rate",
			{
				"args": {
					"item_code": code,
					"warehouse": wh,
					"qty": -1,
					"voucher_type": "Sales Invoice",
					"company": company,
					"posting_date": "2099-01-01",
					"posting_time": "00:00:00",
					"batch_no": d["batch"],
				}
			},
		)
		yield (
			"erpnext.item_dashboard",
			"post",
			"erpnext.stock.dashboard.item_dashboard.get_data",
			{"item_code": code},
		)
		yield (
			"erpnext.item_details_sale",
			"post",
			"erpnext.stock.get_item_details.get_item_details",
			{
				"ctx": {
					"item_code": code,
					"company": company,
					"doctype": "Sales Invoice",
					"currency": ctx["currency"],
					"conversion_rate": 1,
					"price_list": ctx["price_list"],
					"plc_conversion_rate": 1,
					"price_list_currency": ctx["currency"],
					"customer": ctx["customer"],
					"warehouse": wh,
					"qty": 1,
					"update_stock": 1,
					"is_pos": 1,
				}
			},
		)
		yield (
			"erpnext.item_details_buying_list",
			"post",
			"erpnext.stock.get_item_details.get_item_details",
			{
				"ctx": {
					"item_code": code,
					"company": company,
					"doctype": "Purchase Order",
					"currency": ctx["currency"],
					"conversion_rate": 1,
					"price_list": ctx["buying_price_list"],
					"plc_conversion_rate": 1,
					"price_list_currency": ctx["currency"],
					"supplier": ctx.get("supplier"),
					"warehouse": wh,
					"qty": 1,
				}
			},
		)
		yield (
			"erpnext.price_list_rate",
			"post",
			"erpnext.stock.get_item_details.apply_price_list",
			{
				"ctx": {
					"items": [{"item_code": code, "doctype": "Purchase Order Item", "name": "x", "qty": 1}],
					"company": company,
					"doctype": "Purchase Order",
					"currency": ctx["currency"],
					"conversion_rate": 1,
					"price_list": ctx["buying_price_list"],
					"plc_conversion_rate": 1,
					"price_list_currency": ctx["currency"],
				}
			},
		)
		yield (
			"erpnext.quick_stock_balance",
			"post",
			"erpnext.stock.doctype.quick_stock_balance.quick_stock_balance.get_stock_item_details",
			{"warehouse": wh, "date": "2099-01-01", "item": code},
		)
		yield (
			"erpnext.warehouse_details",
			"post",
			"erpnext.stock.doctype.stock_entry.stock_entry.get_warehouse_details",
			{
				"args": {
					"item_code": code,
					"warehouse": wh,
					"company": company,
					"posting_date": "2099-01-01",
					"posting_time": "00:00:00",
					"qty": 1,
				}
			},
		)
		yield (
			"erpnext.stock_reco_balance",
			"post",
			"erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_stock_balance_for",
			{
				"item_code": code,
				"warehouse": wh,
				"posting_date": "2099-01-01",
				"posting_time": "00:00:00",
				"batch_no": d["batch"],
			},
		)
		yield (
			"erpnext.stock_value_chart",
			"post",
			"erpnext.stock.dashboard_chart_source.warehouse_wise_stock_value.warehouse_wise_stock_value.get",
			{"chart": {"name": "cost probe"}, "no_cache": 1, "filters": {"company": company}},
		)
		yield (
			"erpnext.item_group_value_chart",
			"post",
			"erpnext.stock.dashboard_chart_source.stock_value_by_item_group.stock_value_by_item_group.get",
			{"chart": {"name": "cost probe"}, "no_cache": 1, "filters": {"company": company}},
		)
		# PharmacyOS's own pages (every pharmacy role opens them): quantities yes, costs no
		yield "pharmacyos.dashboard", "get", "pharmacyos_erp.pharmacy.dashboard.get_dashboard", {}
		yield "pharmacyos.batches", "get", "pharmacyos_erp.pharmacy.expiry.get_batches", {"search": code}
		yield (
			"pharmacyos.expiry_intelligence",
			"get",
			"pharmacyos_erp.pharmacy.expiry.get_expiry_intelligence",
			{"horizon": 0},
		)
		yield (
			"pharmacyos.item_expiry",
			"get",
			"pharmacyos_erp.pharmacy.expiry.get_item_expiry_summary",
			{"item_code": code},
		)
		yield (
			"pharmacyos.inventory_health",
			"get",
			"pharmacyos_erp.pharmacy.inventory.get_inventory_health",
			{"search": code},
		)
		yield (
			"pharmacyos.reorder",
			"get",
			"pharmacyos_erp.pharmacy.inventory.get_reorder_suggestions",
			{"search": code},
		)
		# Frappe's generic dashboard chart / number card aggregate any field of a readable DocType
		yield (
			"frappe.group_by_chart",
			"post",
			"frappe.desk.doctype.dashboard_chart.dashboard_chart.get",
			{
				"chart": {
					"name": "cost probe",
					"chart_type": "Group By",
					"document_type": "Bin",
					"group_by_type": "Sum",
					"group_by_based_on": "item_code",
					"aggregate_function_based_on": "stock_value",
					"filters_json": json.dumps([["Bin", "item_code", "=", code]]),
				},
				"no_cache": 1,
			},
		)
		yield (
			"frappe.number_card",
			"post",
			"frappe.desk.doctype.number_card.number_card.get_result",
			{
				"doc": {
					"name": "cost probe",
					"type": "Document Type",
					"document_type": "Bin",
					"function": "Sum",
					"aggregate_function_based_on": "stock_value",
					"filters_json": json.dumps([["Bin", "item_code", "=", code, False]]),
				},
				"filters": json.dumps([["Bin", "item_code", "=", code, False]]),
			},
		)
		# inference: filtering on a hidden cost field must not answer differently either side of the value
		stock_value = round(VALUATION * (RECEIVED - 1), 2)
		for side, threshold in (("below", stock_value - 0.5), ("above", stock_value + 0.5)):
			yield (
				f"inference.bin_stock_value_{side}",
				"post",
				"frappe.client.get_list",
				{
					"doctype": "Bin",
					"filters": [["item_code", "=", code], ["stock_value", ">", threshold]],
					"fields": ["name"],
				},
			)
		for side, threshold in (("below", LAST_PURCHASE - 0.5), ("above", LAST_PURCHASE + 0.5)):
			yield (
				f"inference.item_purchase_rate_{side}",
				"post",
				"frappe.client.get_list",
				{
					"doctype": "Item",
					"filters": [["name", "=", code], ["last_purchase_rate", ">", threshold]],
					"fields": ["name"],
				},
			)
		for report in ctx.get("reports", REPORTS):
			filters = {
				"company": company,
				"from_date": "2000-01-01",
				"to_date": "2099-12-31",
				"item_code": code,
				"warehouse": wh,
				"price_list": ctx["buying_price_list"],
				"items": "Enabled Items only",
				"range": "30, 60, 90",
				"valuation_field_type": "Currency",
				"based_on": "Item",
				"period": "Monthly",
			}
			yield (
				f"report:{report}",
				"post",
				"frappe.desk.query_report.run",
				{"report_name": report, "filters": filters},
			)

	def sale_doc(self, code):
		"""A paid counter sale of the probe medicine, as the role under test would post it."""
		ctx = self.ctx
		return {
			"doctype": "Sales Invoice",
			"company": ctx["company"],
			"customer": ctx["customer"],
			"currency": ctx["currency"],
			"is_pos": 1,
			"update_stock": 1,
			"items": [
				{
					"item_code": code,
					"qty": 1,
					"rate": 9900,
					"warehouse": ctx["warehouse"],
					"batch_no": self.data["batch"],
					"use_serial_batch_fields": 1,
					**ctx.get("row_extra", {}),
				}
			],
			"payments": [{"mode_of_payment": "Cash", "account": ctx["cash_account"], "amount": 9900}],
			**ctx.get("sale_extra", {}),
		}

	def run(self, profiles=None):
		self.prepare()
		answers = {}
		for profile, client in self.clients.items():
			if profiles and profile not in profiles:
				continue
			draft = None
			for name, http, method, data in self.probes():
				if http == "get":
					status, body = client.get(
						method,
						{k: json.dumps(v) if isinstance(v, dict | list) else v for k, v in data.items()},
					)
				elif http == "insert_draft":
					status, body = client.post("frappe.client.insert", {"doc": data})
					draft = body.get("message") if status == 200 and isinstance(body, dict) else None
				elif http in ("save_draft", "submit_draft"):
					if not draft:
						self.results.append((profile, name, "skip", []))
						continue
					status, body = client.post(
						"frappe.client.save" if http == "save_draft" else "frappe.client.submit",
						{"doc": draft},
					)
					if http == "save_draft" and status == 200 and isinstance(body, dict):
						draft = body.get("message") or draft
				elif http in ("rest_post", "rest_v2_post"):
					resource = getattr(client, "resource", None)
					if not resource:
						self.results.append((profile, name, "skip", []))
						continue
					status, body = resource(method, data, v2=http == "rest_v2_post")
				else:
					status, body = client.post(method, data)
				text = json.dumps(body, default=str) if status == 200 else ""
				leaked = sorted(s for s in self.secrets if s in text)
				self.results.append((profile, name, status, leaked))
				if name.startswith("inference.") and status == 200:
					answers.setdefault((profile, name.rsplit("_", 1)[0]), []).append(
						len(body.get("message") or [])
					)
		for (profile, probe), counts in answers.items():
			if len(counts) == 2 and counts[0] != counts[1]:
				# the answer depends on the hidden value: it can be found by bisection
				self.results.append((profile, probe, 200, ["inferred by filtering"]))
		return self.results

	def leaks(self, profile):
		return [(name, status, leaked) for p, name, status, leaked in self.results if p == profile and leaked]

	def table(self):
		probes = list(dict.fromkeys(r[1] for r in self.results))
		profiles = list(dict.fromkeys(r[0] for r in self.results))
		cells = {(p, n): (s, lk) for p, n, s, lk in self.results}
		lines = ["| probe | " + " | ".join(profiles) + " |", "|---|" + "---|" * len(profiles)]
		for n in probes:
			row = []
			for p in profiles:
				s, lk = cells.get((p, n), ("", []))
				row.append(f"**LEAK** {s}" if lk else str(s))
			lines.append(f"| {n} | " + " | ".join(row) + " |")
		return "\n".join(lines)


# query reports that show costs; the probe asks each as the user (a refused or empty answer passes)
REPORTS = (
	"Stock Balance",
	"Stock Ledger",
	"Stock Projected Qty",
	"Item Prices",
	"Item Price Stock",
	"Batch-Wise Balance History",
	"Batch Item Expiry Status",
	"Stock Ageing",
	"Warehouse wise Item Balance Age and Value",
	"Gross Profit",
	"Stock and Account Value Comparison",
	"Item-wise Price List Rate",
	"Total Stock Summary",
	"Stock Analytics",
	"Incorrect Stock Value Report",
	"Serial and Batch Summary",
	"Available Batch Report",
	"FIFO Queue vs Qty After Transaction Comparison",
	"Stock Ledger Variance",
	"Cogs By Item Group",
	"Product Bundle Balance",
	"Item Shortage Report",
	"Inactive Sales Items",
	"Itemwise Recommended Reorder Level",
)
