#!/usr/bin/env python3
"""Cost-confidentiality sentinel matrix against a running PharmacyOS ERP server (real HTTP, API keys).

Seeds a fresh medicine with four unmistakable sentinel values — the last purchase rate, the Item's own
valuation rate, the receipt valuation (which becomes the Bin valuation, the stock ledger's incoming rate,
the batch bundle's average rate and the sale's `incoming_rate`) and a buying price — then requests
every surface of the round-4 brief as each role profile and searches the answer for those values:
Item, Bin, Item Price, the stock ledger and batch bundle, the role's own sale through every write
and read endpoint (insert / save / submit / REST v1 / REST v2 / get / list / versions / print),
dashboards and number cards, the item-detail, search and POS helpers, and the query reports.

	python3 cost_matrix_check.py --site <site> --actors actors.json [--url http://127.0.0.1:8000]

`actors.json` maps role profile → {"user", "key", "secret"} (see permission_check.py). Counter profiles
(Cashier, Pharmacist), the integration account and Guest must never receive a sentinel; cost-reading
profiles must (control). Prints the matrix as Markdown and exits non-zero on any leak.
Creates uniquely named test documents: never point it at a production site.
"""

import argparse
import json
import sys
import uuid

import requests

COMPANY = "_Test Company"
WH = "_Test Warehouse - _TC"
CUSTOMER = "_Test Customer"
CASH = "Cash - _TC"
ROW_ACCOUNTS = {
	"income_account": "Sales - _TC",
	"expense_account": "Cost of Goods Sold - _TC",
	"cost_center": "_Test Cost Center - _TC",
}
NO_COST_PROFILES = ("Cashier", "Pharmacist", "PharmacyOS Integration", "Guest")
COST_PROFILES = ("Pharmacy Owner", "Pharmacy Manager", "Inventory Manager", "Pharmacy Accountant")


class Api:
	def __init__(self, actor, url="http://127.0.0.1:8000", site="test.localhost"):
		self.url, self.site = url, site
		self.s = requests.Session()
		self.s.headers.update({"Host": site, "Accept": "application/json"})
		if actor:
			self.s.headers["Authorization"] = f"token {actor['key']}:{actor['secret']}"

	def session(self):
		s = requests.Session()
		s.headers.update(self.s.headers)
		return s

	def call(self, method, session=None, http="post", **data):
		s = session or self.s
		if http == "get":
			params = {k: json.dumps(v) if isinstance(v, dict | list) else v for k, v in data.items()}
			return s.get(f"{self.url}/api/method/{method}", params=params, timeout=180)
		return s.post(f"{self.url}/api/method/{method}", json=data, timeout=180)

	def ok(self, method, **data):
		r = self.call(method, **data)
		if r.status_code != 200:
			raise RuntimeError(f"{method}: {r.status_code} {r.text[:700]}")
		return r.json().get("message")

	def get_list(self, doctype, **kw):
		return self.ok("frappe.client.get_list", doctype=doctype, limit_page_length=0, **kw)

	def resource(self, http, path, **data):
		return getattr(self.s, http)(f"{self.url}/api/resource/{path}", json=data or None, timeout=180)


def classify(r):
	if isinstance(r, tuple):
		return ("exception", r[1])
	try:
		body = r.json()
	except Exception:
		body = {}
	exc = body.get("exc_type") or ""
	msg = body.get("exception") or ""
	if not msg and body.get("_server_messages"):
		try:
			msg = " | ".join(json.loads(m).get("message", "") for m in json.loads(body["_server_messages"]))
		except Exception:
			msg = body["_server_messages"]
	return (r.status_code, exc, msg[:220])


p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
p.add_argument("--actors", required=True)
p.add_argument("--url", default="http://127.0.0.1:8000")
p.add_argument("--site", required=True)
args = p.parse_args()
actors = json.load(open(args.actors))
ctx = {"url": args.url, "site": args.site}
admin = Api(actors["Administrator"], **ctx)

SENTINELS = {
	"purchase_rate(last_purchase_rate)": 6421.33,
	"master_valuation(Item.valuation_rate)": 8137.19,
	"receipt_valuation(Bin/SLE/bundle/incoming_rate)": 7319.17,
	"buying_price(Item Price buying)": 5213.77,
}
RECEIVED = 10


def seed():
	code = "CM-" + uuid.uuid4().hex[:8].upper()
	admin.ok(
		"pharmacyos_erp.pharmacy.medicine.create_medicine",
		item_code=code,
		item_name=f"Cost matrix {code}",
		item_group="Products",
		stock_uom="Nos",
		standard_rate=9900,
	)
	batch = admin.ok(
		"pharmacyos_erp.pharmacy.receiving.create_receiving_batch",
		item_code=code,
		batch_id=f"CMB-{code}",
		expiry_date="2099-12-31",
	)["name"]
	admin.ok(
		"frappe.client.insert",
		doc={
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": COMPANY,
			"docstatus": 1,
			"items": [
				{
					"item_code": code,
					"qty": RECEIVED,
					"t_warehouse": WH,
					"basic_rate": 7319.17,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
				}
			],
		},
	)
	buying = admin.ok(
		"frappe.client.insert",
		doc={
			"doctype": "Item Price",
			"item_code": code,
			"price_list": "Standard Buying",
			"price_list_rate": 5213.77,
		},
	)["name"]
	admin.ok(
		"frappe.client.set_value",
		doctype="Item",
		name=code,
		fieldname={"last_purchase_rate": 6421.33, "valuation_rate": 8137.19},
	)
	bin_name = admin.ok(
		"frappe.client.get_value",
		doctype="Bin",
		filters={"item_code": code, "warehouse": WH},
		fieldname="name",
	)["name"]
	return {"code": code, "batch": batch, "buying": buying, "bin": bin_name}


def sale_doc(code, batch, qty=1):
	return {
		"doctype": "Sales Invoice",
		"company": COMPANY,
		"customer": CUSTOMER,
		"currency": "INR",
		"debit_to": "Debtors - _TC",
		"is_pos": 1,
		"update_stock": 1,
		"items": [
			{
				"item_code": code,
				"qty": qty,
				"rate": 9900,
				"warehouse": WH,
				"batch_no": batch,
				"use_serial_batch_fields": 1,
				**ROW_ACCOUNTS,
			}
		],
		"payments": [{"mode_of_payment": "Cash", "account": CASH, "amount": qty * 9900}],
	}


def secrets(qty_on_hand):
	vals = set(SENTINELS.values()) | {round(7319.17 * RECEIVED, 2), round(7319.17 * qty_on_hand, 2)}
	return sorted({f"{v:.2f}".rstrip("0").rstrip(".") for v in vals})


def leaked(text, secret_strings):
	return [s for s in secret_strings if s in text]


def keys_with(obj, secret_strings, path="", found=None):
	found = [] if found is None else found
	if isinstance(obj, dict):
		for k, v in obj.items():
			keys_with(v, secret_strings, f"{path}.{k}" if path else k, found)
	elif isinstance(obj, list):
		for i, v in enumerate(obj):
			keys_with(v, secret_strings, f"{path}[{i}]", found)
	else:
		s = json.dumps(obj, default=str)
		if any(x in s for x in secret_strings):
			found.append(path)
	return found


REPORTS = [
	"Stock Balance",
	"Stock Ledger",
	"Stock Projected Qty",
	"Item Prices",
	"Item Price Stock",
	"Batch-Wise Balance History",
	"Stock Ageing",
	"Warehouse wise Item Balance Age and Value",
	"Gross Profit",
	"Stock and Account Value Comparison",
	"Total Stock Summary",
	"Stock Analytics",
	"Incorrect Stock Value Report",
	"Serial and Batch Summary",
	"Cogs By Item Group",
	"Item-wise Sales Register",
	"Sales Register",
	"Sales Analytics",
]


def probes(d, own_sale):
	code, wh = d["code"], WH
	yield "item.client_get", "post", "frappe.client.get", {"doctype": "Item", "name": code}
	yield "item.form_load", "get", "frappe.desk.form.load.getdoc", {"doctype": "Item", "name": code}
	yield (
		"item.get_list_star",
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
	yield "item.rest_v1", "rest", f"Item/{code}", {}
	yield "item.rest_v2", "restv2", f"document/Item/{code}", {}
	yield (
		"item.helper_item_get_item_details",
		"post",
		"erpnext.stock.doctype.item.item.get_item_details",
		{"item_code": code, "company": COMPANY},
	)
	yield (
		"item.helper_get_item_prices",
		"post",
		"erpnext.stock.doctype.item.item.get_item_prices",
		{"item_code": code},
	)
	yield (
		"item.helper_last_purchase_details",
		"post",
		"erpnext.stock.doctype.item.item.get_last_purchase_details",
		{"item_code": code},
	)
	yield (
		"item.search_link",
		"post",
		"frappe.desk.search.search_link",
		{"doctype": "Item", "txt": code, "page_length": 5},
	)
	yield (
		"item.search_widget",
		"post",
		"frappe.desk.search.search_widget",
		{"doctype": "Item", "txt": code, "page_length": 5},
	)
	yield (
		"item.item_dashboard",
		"post",
		"erpnext.stock.dashboard.item_dashboard.get_data",
		{"item_code": code},
	)
	yield (
		"item.sales_item_details",
		"post",
		"erpnext.stock.get_item_details.get_item_details",
		{
			"ctx": {
				"item_code": code,
				"company": COMPANY,
				"doctype": "Sales Invoice",
				"currency": "INR",
				"conversion_rate": 1,
				"price_list": "Standard Selling",
				"plc_conversion_rate": 1,
				"price_list_currency": "INR",
				"customer": CUSTOMER,
				"warehouse": wh,
				"qty": 1,
				"update_stock": 1,
				"is_pos": 1,
			}
		},
	)
	yield (
		"item.buying_item_details",
		"post",
		"erpnext.stock.get_item_details.get_item_details",
		{
			"ctx": {
				"item_code": code,
				"company": COMPANY,
				"doctype": "Purchase Order",
				"currency": "INR",
				"conversion_rate": 1,
				"price_list": "Standard Buying",
				"plc_conversion_rate": 1,
				"price_list_currency": "INR",
				"warehouse": wh,
				"qty": 1,
			}
		},
	)
	yield (
		"item.get_valuation_rate",
		"post",
		"erpnext.stock.get_item_details.get_valuation_rate",
		{"item_code": code, "company": COMPANY, "warehouse": wh},
	)
	yield (
		"item.get_incoming_rate",
		"post",
		"erpnext.stock.utils.get_incoming_rate",
		{
			"args": {
				"item_code": code,
				"warehouse": wh,
				"qty": -1,
				"voucher_type": "Sales Invoice",
				"company": COMPANY,
				"posting_date": "2099-01-01",
				"posting_time": "00:00:00",
				"batch_no": d["batch"],
			}
		},
	)
	yield (
		"item.get_stock_balance_valuation",
		"post",
		"erpnext.stock.utils.get_stock_balance",
		{"item_code": code, "warehouse": wh, "with_valuation_rate": 1},
	)
	yield (
		"item.pos_get_items",
		"post",
		"erpnext.selling.page.point_of_sale.point_of_sale.get_items",
		{
			"start": 0,
			"page_length": 20,
			"price_list": "Standard Selling",
			"item_group": "Products",
			"search_term": code,
			"pos_profile": "",
		},
	)
	yield (
		"item.batch_qty",
		"post",
		"erpnext.stock.doctype.batch.batch.get_batch_qty",
		{"batch_no": d["batch"], "warehouse": wh, "item_code": code},
	)
	yield (
		"bin.get_list_star",
		"post",
		"frappe.client.get_list",
		{"doctype": "Bin", "filters": {"item_code": code}, "fields": ["*"]},
	)
	yield "bin.client_get", "post", "frappe.client.get", {"doctype": "Bin", "name": d["bin"]}
	yield (
		"bin.get_value",
		"post",
		"frappe.client.get_value",
		{"doctype": "Bin", "filters": d["bin"], "fieldname": ["stock_value", "valuation_rate"]},
	)
	yield "bin.rest_v1", "rest", f"Bin/{d['bin']}", {}
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
		"bin.group_by_count",
		"post",
		"frappe.desk.reportview.get_group_by_count",
		{"doctype": "Bin", "current_filters": [["Bin", "item_code", "=", code]], "field": "stock_value"},
	)
	yield (
		"bin.dashboard_chart_sum",
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
		"bin.number_card_sum",
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
	yield (
		"chart.warehouse_stock_value",
		"post",
		"erpnext.stock.dashboard_chart_source.warehouse_wise_stock_value.warehouse_wise_stock_value.get",
		{"chart": {"name": "cost probe"}, "no_cache": 1, "filters": {"company": COMPANY}},
	)
	yield (
		"chart.item_group_stock_value",
		"post",
		"erpnext.stock.dashboard_chart_source.stock_value_by_item_group.stock_value_by_item_group.get",
		{"chart": {"name": "cost probe"}, "no_cache": 1, "filters": {"company": COMPANY}},
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
	yield "item_price.buying_get", "post", "frappe.client.get", {"doctype": "Item Price", "name": d["buying"]}
	yield "item_price.buying_rest", "rest", f"Item Price/{d['buying']}", {}
	yield (
		"item_price.price_list_rate_buying",
		"post",
		"erpnext.stock.get_item_details.apply_price_list",
		{
			"ctx": {
				"items": [{"item_code": code, "doctype": "Purchase Order Item", "name": "x", "qty": 1}],
				"company": COMPANY,
				"doctype": "Purchase Order",
				"currency": "INR",
				"conversion_rate": 1,
				"price_list": "Standard Buying",
				"plc_conversion_rate": 1,
				"price_list_currency": "INR",
			}
		},
	)
	yield (
		"sle.get_list_star",
		"post",
		"frappe.client.get_list",
		{"doctype": "Stock Ledger Entry", "filters": {"item_code": code}, "fields": ["*"]},
	)
	yield (
		"sle.reportview",
		"post",
		"frappe.desk.reportview.get",
		{
			"doctype": "Stock Ledger Entry",
			"filters": [["item_code", "=", code]],
			"fields": ["`tabStock Ledger Entry`.`incoming_rate`", "`tabStock Ledger Entry`.`stock_value`"],
		},
	)
	yield (
		"bundle.get_list",
		"post",
		"frappe.client.get_list",
		{
			"doctype": "Serial and Batch Bundle",
			"filters": {"item_code": code},
			"fields": ["name", "avg_rate", "total_amount"],
		},
	)
	# the user's own sale (or the administrator's when the role cannot sell): every response shape
	yield "sale.insert_draft_response", "insert_draft", None, sale_doc(code, d["batch"])
	yield "sale.save_response", "save_draft", None, {}
	yield "sale.submit_response", "submit_draft", None, {}
	yield "sale.rest_v1_post_response", "rest_post", "Sales Invoice", sale_doc(code, d["batch"])
	yield "sale.rest_v2_create_response", "restv2_post", "document/Sales Invoice", sale_doc(code, d["batch"])
	yield "sale.client_get", "post", "frappe.client.get", {"doctype": "Sales Invoice", "name": own_sale}
	yield (
		"sale.form_load_docinfo",
		"get",
		"frappe.desk.form.load.getdoc",
		{"doctype": "Sales Invoice", "name": own_sale},
	)
	yield "sale.rest_v1_get", "rest", f"Sales Invoice/{own_sale}", {}
	yield "sale.rest_v2_get", "restv2", f"document/Sales Invoice/{own_sale}", {}
	yield (
		"sale.item_list",
		"post",
		"frappe.client.get_list",
		{
			"doctype": "Sales Invoice",
			"filters": {"name": own_sale},
			"fields": ["`tabSales Invoice Item`.`incoming_rate`"],
		},
	)
	yield (
		"sale.child_get_list",
		"post",
		"frappe.client.get_list",
		{
			"doctype": "Sales Invoice Item",
			"parent": "Sales Invoice",
			"filters": {"parent": own_sale},
			"fields": ["incoming_rate", "item_code"],
		},
	)
	yield (
		"sale.versions",
		"post",
		"frappe.client.get_list",
		{
			"doctype": "Version",
			"filters": {"ref_doctype": "Sales Invoice", "docname": own_sale},
			"fields": ["name", "data"],
		},
	)
	yield (
		"sale.print_html",
		"post",
		"frappe.www.printview.get_html_and_style",
		{
			"doc": json.dumps({"doctype": "Sales Invoice", "name": own_sale}),
			"print_format": "Standard",
			"no_letterhead": 1,
		},
	)
	yield (
		"sale.run_doc_method_submit_echo",
		"post",
		"run_doc_method",
		{"dt": "Sales Invoice", "dn": own_sale, "method": "get_stock_items"},
	)
	for report in REPORTS:
		yield (
			f"report.{report}",
			"post",
			"frappe.desk.query_report.run",
			{
				"report_name": report,
				"filters": {
					"company": COMPANY,
					"from_date": "2000-01-01",
					"to_date": "2099-12-31",
					"item_code": code,
					"warehouse": wh,
					"price_list": "Standard Buying",
					"items": "Enabled Items only",
					"range": "30, 60, 90",
					"valuation_field_type": "Currency",
					"based_on": "Item",
					"period": "Monthly",
					"doc_type": "Sales Invoice",
					"doctype": "Sales Invoice",
					"value_quantity": "Value",
				},
			},
		)
	yield (
		"report.export_stock_balance_csv",
		"export",
		"Stock Balance",
		{
			"company": COMPANY,
			"from_date": "2000-01-01",
			"to_date": "2099-12-31",
			"item_code": code,
			"warehouse": wh,
		},
	)
	yield (
		"report.export_gross_profit_csv",
		"export",
		"Gross Profit",
		{"company": COMPANY, "from_date": "2000-01-01", "to_date": "2099-12-31", "group_by": "Invoice"},
	)


def run_for(profile, api, d, secret_strings):
	results = {}
	# the role's own sale, if it may sell
	own = api.call("frappe.client.insert", doc={**sale_doc(d["code"], d["batch"]), "docstatus": 1})
	own_sale = own.json()["message"]["name"] if own.status_code == 200 else d["admin_sale"]
	results["_own_sale"] = (own.status_code, own_sale)
	draft = None
	for name, http, method, data in probes(d, own_sale):
		try:
			if http == "post":
				r = api.call(method, **data)
			elif http == "get":
				r = api.call(method, http="get", **data)
			elif http == "rest":
				r = api.resource("get", method)
			elif http == "restv2":
				r = api.s.get(f"{api.url}/api/v2/{method}", timeout=120)
			elif http == "rest_post":
				r = api.resource("post", method, **data)
			elif http == "restv2_post":
				r = api.s.post(f"{api.url}/api/v2/{method}", json=data, timeout=120)
			elif http == "insert_draft":
				r = api.call("frappe.client.insert", doc=data)
				draft = r.json().get("message") if r.status_code == 200 else None
			elif http == "save_draft":
				if not draft:
					results[name] = ("skip", [], [])
					continue
				r = api.call("frappe.client.save", doc=draft)
				draft = r.json().get("message") if r.status_code == 200 else draft
			elif http == "submit_draft":
				if not draft:
					results[name] = ("skip", [], [])
					continue
				r = api.call("frappe.client.submit", doc=draft)
			elif http == "export":
				r = api.s.post(
					f"{api.url}/api/method/frappe.desk.query_report.export_query",
					data={
						"report_name": method,
						"file_format_type": "CSV",
						"filters": json.dumps(data),
						"visible_idx": "[]",
						"csv_quoting": "1",
						"csv_delimiter": ",",
					},
					timeout=180,
				)
			text = r.text if r.status_code == 200 else ""
			body = None
			try:
				body = r.json() if r.status_code == 200 else None
			except Exception:
				body = None
			found = leaked(text, secret_strings)
			paths = (
				keys_with(body, secret_strings)[:6]
				if body is not None and found
				else ([] if not found else ["(non-JSON body)"])
			)
			results[name] = (r.status_code, found, paths)
		except Exception as e:
			results[name] = ("error", [], [repr(e)[:120]])
	return results


d = seed()
admin_sale = admin.ok("frappe.client.insert", doc={**sale_doc(d["code"], d["batch"]), "docstatus": 1})["name"]
d["admin_sale"] = admin_sale
secret_strings = secrets(RECEIVED - 1)
profiles = [
	p
	for p in (
		"Pharmacy Owner",
		"Pharmacy Manager",
		"Inventory Manager",
		"Pharmacy Accountant",
		"Branch Manager",
		"Purchasing Officer",
		"Cashier",
		"Pharmacist",
		"PharmacyOS Integration",
	)
	if p in actors
] + ["Guest"]
matrix = {}
for profile in profiles:
	api = Api(actors[profile], **ctx) if profile != "Guest" else Api(None, **ctx)
	matrix[profile] = run_for(profile, api, d, secret_strings)
	print(profile, "done", file=sys.stderr)
probes_seen = [k for k in matrix[profiles[0]] if not k.startswith("_")]
print("| probe | " + " | ".join(p.replace("PharmacyOS ", "") for p in profiles) + " |")
print("|---|" + "---|" * len(profiles))
for name in probes_seen:
	cells = []
	for p in profiles:
		status, found, _paths = matrix[p].get(name, ("", [], []))
		cells.append(f"**LEAK** {status}" if found else str(status))
	print(f"| {name} | " + " | ".join(cells) + " |")
failures = []
for p in profiles:
	leaks = [(k, v) for k, v in matrix[p].items() if not k.startswith("_") and v[1]]
	if p in NO_COST_PROFILES:
		failures += [f"LEAK {p}: {k} ({v[0]}) {v[1]} at {v[2]}" for k, v in leaks]
	elif p in COST_PROFILES and not leaks:
		failures.append(f"CONTROL {p}: no probe saw the sentinels — the probe proves nothing")
for f in failures:
	print(f)
print(
	json.dumps(
		{"checks": sum(len(m) - 1 for m in matrix.values()), "profiles": profiles, "failures": len(failures)}
	)
)
sys.exit(1 if failures else 0)
