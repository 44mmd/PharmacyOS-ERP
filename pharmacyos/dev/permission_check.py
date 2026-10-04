#!/usr/bin/env python3
"""Staff / API permission matrix against a running PharmacyOS ERP server (real HTTP, API keys).

Runs the same matrix as the test suite (`pharmacyos_erp/tests/permission_matrix.py`) as each role,
over the network, against gunicorn. Needs a JSON file mapping role profile → {"user", "key",
"secret"} for: Administrator (prepares data), Pharmacy Owner, Pharmacy Manager, Cashier, Pharmacist,
Pharmacy Accountant, Inventory Manager, PharmacyOS Integration. Each staff user must hold exactly
the role profile it is listed under. Guest is tested without credentials.

	python3 permission_check.py --site <site> --actors actors.json [--url http://127.0.0.1:8000]

Prints the matrix as a Markdown table and exits non-zero on any mismatch. Creates uniquely named
test medicines and documents: never point it at a production site.

	python3 permission_check.py --site <site> --actors actors.json --cost

runs the cost-data red-team instead (`tests/cost_exposure.py`): every profile's REST/RPC/report
access is searched for the purchase price, valuation and stock value of a fresh medicine. Counter
profiles (Cashier, Pharmacist) and the integration account must never receive them; cost-reading
profiles must (control).
"""

import argparse
import datetime
import importlib.util
import json
import pathlib
import sys

import requests

MATRIX = (
	pathlib.Path(__file__).resolve().parents[1]
	/ "apps/pharmacyos_erp/pharmacyos_erp/tests/permission_matrix.py"
)


COST = MATRIX.with_name("cost_exposure.py")
NO_COST_PROFILES = ("Cashier", "Pharmacist", "PharmacyOS Integration", "Guest")
COST_PROFILES = ("Pharmacy Owner", "Pharmacy Manager", "Inventory Manager")


def load_module(path, name):
	spec = importlib.util.spec_from_file_location(name, path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def run_cost(admin, clients, ctx):
	probe = load_module(COST, "cost_exposure").CostProbe(admin, clients, ctx)
	probe.run()
	print(probe.table())
	failures = []
	for profile in clients:
		leaks = probe.leaks(profile)
		if profile in NO_COST_PROFILES:
			failures += [f"LEAK {profile}: {name} ({status}) {values}" for name, status, values in leaks]
		elif profile in COST_PROFILES and not leaks:
			failures.append(f"CONTROL {profile}: no probe saw the costs — the probe proves nothing")
	for f in failures:
		print(f)
	print(json.dumps({"checks": len(probe.results), "failures": len(failures)}))
	sys.exit(1 if failures else 0)


def load_matrix():
	spec = importlib.util.spec_from_file_location("permission_matrix", MATRIX)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class Http:
	def __init__(self, url, site, key=None, secret=None):
		self.url = url.rstrip("/")
		self.session = requests.Session()
		self.session.headers.update({"Host": site, "Accept": "application/json"})
		if key:
			self.session.headers["Authorization"] = f"token {key}:{secret}"

	def _out(self, r):
		try:
			return r.status_code, r.json()
		except ValueError:
			return r.status_code, {"raw": r.text[:300]}

	def post(self, method, data):
		return self._out(self.session.post(f"{self.url}/api/method/{method}", json=data, timeout=120))

	def get(self, method, params):
		return self._out(self.session.get(f"{self.url}/api/method/{method}", params=params, timeout=120))


def discover(admin):
	def msg(method, **data):
		status, body = admin.post(method, data)
		if status != 200:
			raise RuntimeError(f"{method}: {status} {str(body)[:300]}")
		return body["message"]

	branch = msg(
		"frappe.client.get_list",
		doctype="Branch",
		filters={"pharmacyos_storefront_code": ["is", "set"]},
		fields=["pharmacyos_warehouse", "pharmacyos_storefront_code"],
	)[0]
	company = msg(
		"frappe.client.get_value",
		doctype="Warehouse",
		filters=branch["pharmacyos_warehouse"],
		fieldname="company",
	)["company"]
	acc = msg(
		"frappe.client.get_value",
		doctype="Company",
		filters=company,
		fieldname=[
			"default_currency",
			"default_receivable_account",
			"default_income_account",
			"default_cash_account",
		],
	)
	customer = "Permission Check Customer"
	if not msg("frappe.client.get_list", doctype="Customer", filters={"customer_name": customer}):
		group = msg("frappe.client.get_list", doctype="Customer Group", filters={"is_group": 0})[0]["name"]
		msg(
			"frappe.client.insert",
			doc={"doctype": "Customer", "customer_name": customer, "customer_group": group},
		)
	customer = msg("frappe.client.get_list", doctype="Customer", filters={"customer_name": customer})[0][
		"name"
	]
	if not msg("frappe.client.get_list", doctype="Address", filters={"address_title": customer}):
		# a real customer has an address: the invoice renders it as the seller
		msg(
			"frappe.client.insert",
			doc={
				"doctype": "Address",
				"address_title": customer,
				"address_type": "Billing",
				"address_line1": "شارع الرشيد",
				"city": "Baghdad",
				"country": "Iraq",
				"is_primary_address": 1,
				"links": [{"link_doctype": "Customer", "link_name": customer}],
			},
		)
	mop = msg("frappe.client.get", doctype="Mode of Payment", name="Cash")
	if not any(a["company"] == company for a in mop["accounts"]):
		mop["accounts"].append({"company": company, "default_account": acc["default_cash_account"]})
		msg("frappe.client.save", doc=mop)
	return {
		"company": company,
		"warehouse": branch["pharmacyos_warehouse"],
		"branch_code": branch["pharmacyos_storefront_code"],
		"customer": customer,
		"currency": acc["default_currency"],
		"cash_account": acc["default_cash_account"],
		"receivable_account": acc["default_receivable_account"],
		"income_account": acc["default_income_account"],
		"price_list": "Standard Selling",
		"buying_price_list": "Standard Buying",
		"today": datetime.date.today().isoformat(),
	}


def pos_profiles(admin, ctx, actors):
	"""One counter (POS Profile) per staff user and run: ERPNext allows one open shift per counter,
	and each run opens a shift on it."""
	out = {}
	run = datetime.datetime.now().strftime("%H%M%S")
	for profile, actor in actors.items():
		if profile in ("Administrator", "PharmacyOS Integration"):
			continue
		name = f"Matrix Counter {run} {actor['user']}"
		status, body = admin.post(
			"frappe.client.get_list", {"doctype": "POS Profile", "filters": {"name": name}}
		)
		if not body.get("message"):
			status, body = admin.post(
				"frappe.client.insert",
				{
					"doc": {
						"doctype": "POS Profile",
						"__newname": name,
						"name": name,
						"company": ctx["company"],
						"warehouse": ctx["warehouse"],
						"currency": ctx["currency"],
						"customer": ctx["customer"],
						"selling_price_list": ctx["price_list"],
						"write_off_account": ctx["cash_account"],
						"write_off_cost_center": admin.post(
							"frappe.client.get_value",
							{"doctype": "Company", "filters": ctx["company"], "fieldname": "cost_center"},
						)[1]["message"]["cost_center"],
						"update_stock": 1,
						"payments": [{"mode_of_payment": "Cash", "default": 1}],
						"applicable_for_users": [{"user": actor["user"]}],
					}
				},
			)
			if status != 200:
				raise RuntimeError(f"POS Profile: {status} {str(body)[:400]}")
		out[profile] = (name, actor["user"])
	return out


def main():
	p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
	p.add_argument("--url", default="http://127.0.0.1:8000")
	p.add_argument("--site", required=True)
	p.add_argument("--actors", required=True)
	p.add_argument("--only", help="comma-separated operations")
	p.add_argument("--cost", action="store_true", help="run the cost-data red-team instead")
	args = p.parse_args()
	matrix = load_matrix()
	actors = json.load(open(args.actors))
	admin = Http(args.url, args.site, actors["Administrator"]["key"], actors["Administrator"]["secret"])
	clients = {
		profile: Http(args.url, args.site, actors[profile]["key"], actors[profile]["secret"])
		for profile in matrix.PROFILES
		if profile in actors
	}
	clients["Guest"] = Http(args.url, args.site)
	ctx = discover(admin)
	if args.cost:
		run_cost(admin, clients, ctx)
	run = matrix.Matrix(
		admin, clients, ctx, pos_profiles(admin, ctx, {k: v for k, v in actors.items() if k in clients})
	)
	run.run(only=set(args.only.split(",")) if args.only else None)
	print(run.table())
	failures = run.failures()
	for f in failures:
		print(
			f"MISMATCH {f['op']} as {f['profile']}: expected {f['expected']}, got {f['observed']} {f['exc'] or ''} {f['detail']}"
		)
	print(json.dumps({"checks": len(run.results), "failures": len(failures)}))
	sys.exit(1 if failures else 0)


if __name__ == "__main__":
	main()
