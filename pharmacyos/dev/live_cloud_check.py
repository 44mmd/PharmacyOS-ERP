#!/usr/bin/env python3
"""Live PharmacyOS Cloud ↔ ERP acceptance run: a real Cloud server and a real ERP server.

Starts the Cloud backend (uvicorn, throwaway SQLite database) next to a running ERP site, connects
them with a fresh shared secret, and drives the flows through their real HTTP interfaces. The ERP's
two scheduler jobs (`outbox.process_outbox`, `cloud.pull_orders`) are run with `bench execute`, i.e.
the exact code the scheduler runs every minute.

* catalog: a published medicine and its ERP price reach the website;
* N-05: changing only the Item Price (no Item save) reaches the website, stock untouched;
* Cloud outage: events wait while the Cloud is down and arrive after it comes back;
* duplicate delivery: re-sending an already applied event changes nothing;
* website order: checkout retried with the same Idempotency-Key (lost response) → one order; the
  ERP imports it once as a submitted Sales Order (reservation) and acknowledges it;
* completion: the ERP delivers and invoices (FEFO batch), confirms, and the Cloud shows it completed.
* public price only (round 3): customer-specific prices (cheaper, edited, deleted) never reach the
  website; a price valid until today carries its validity; at midnight — with the Cloud down — the
  date job sends the expiry, which arrives after recovery and takes the product off sale (checkout
  refused); a duplicate delivery changes nothing; an order whose price expired before the ERP imported
  it is refused and goes to review; a replacement price puts the product back on sale with its stock.

	python3 live_cloud_check.py --site live.localhost --actors actors.json \
		--cloud-dir /path/to/PharmacyOS/backend --cloud-python /path/to/venv/bin/python

Development / staging only: it changes the site's PharmacyOS Settings (Cloud address, secret, events
on) and enables developer mode for the loopback HTTP address (restart the ERP server afterwards if it
was not in developer mode: plain HTTP is refused otherwise).
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

import requests

CLOUD_PORT = 8001


class Check:
	def __init__(self):
		self.results = []

	def __call__(self, name, ok, detail=""):
		self.results.append((name, bool(ok), detail))
		print(f"{'ok  ' if ok else 'FAIL'} {name} {detail if not ok or detail else ''}".rstrip())
		return ok


class Erp:
	def __init__(self, url, site, key, secret, bench, bench_cmd="bench"):
		self.url, self.site, self.bench, self.bench_cmd = url.rstrip("/"), site, bench, bench_cmd
		self.s = requests.Session()
		self.s.headers.update({"Host": site, "Authorization": f"token {key}:{secret}"})

	def call(self, method, **data):
		r = self.s.post(f"{self.url}/api/method/{method}", json=data, timeout=120)
		if r.status_code != 200:
			raise RuntimeError(f"ERP {method}: {r.status_code} {r.text[:400]}")
		return r.json().get("message")

	def value(self, doctype, name, field):
		return self.call("frappe.client.get_value", doctype=doctype, filters=name, fieldname=field).get(field)

	def job(self, path, args=None):
		"""Run a scheduler job exactly as the scheduler does (bench execute, its own process)."""
		extra = f" --args '{json.dumps(args)}'" if args is not None else ""
		out = subprocess.run(
			[
				"sudo",
				"-u",
				"frappe",
				"-H",
				"bash",
				"-c",
				f"cd {self.bench} && {self.bench_cmd} --site {self.site} execute {path}{extra}",
			],
			capture_output=True,
			text=True,
			timeout=600,
		)
		if out.returncode:
			raise RuntimeError(f"{path}: {out.stderr[-800:]}")
		return out.stdout.strip()


class Cloud:
	def __init__(self, cloud_dir, python, secret):
		self.dir, self.python, self.secret = cloud_dir, python, secret
		self.tmp = tempfile.mkdtemp(prefix="pharmacyos-live-")
		self.db = os.path.join(self.tmp, "cloud.db")
		self.env = {
			**os.environ,
			"DATABASE_URL": f"sqlite:///{self.db}",
			"SECRET_KEY": secrets.token_urlsafe(64),
			"ENVIRONMENT": "development",
			"ALLOWED_HOSTS": "127.0.0.1,localhost,testserver",
			"COOKIE_SECURE": "false",
			"ADMIN_BOOTSTRAP_ENABLED": "false",
			"AUTO_SYNC_CATALOG": "false",
			"STARTUP_MAINTENANCE_ENABLED": "false",
			"DEMO_ENABLED": "false",
			"UPLOAD_ROOT": os.path.join(self.tmp, "uploads"),
			"ERP_INTEGRATION_SECRET": secret,
			"ERP_ORDER_BRANCH_CODE": "MAIN",
			"ERP_CATALOG_AUTHORITATIVE": "true",
		}
		self.proc = None
		self.base = f"http://127.0.0.1:{CLOUD_PORT}"

	def start(self):
		self.proc = subprocess.Popen(
			[self.python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(CLOUD_PORT)],
			cwd=self.dir,
			env=self.env,
			stdout=open(os.path.join(self.tmp, "uvicorn.log"), "a"),
			stderr=subprocess.STDOUT,
		)
		for _ in range(60):
			try:
				if requests.get(f"{self.base}/health", timeout=1).status_code < 500:
					return
			except requests.RequestException:
				pass
			time.sleep(0.5)
		raise RuntimeError("Cloud did not start; see " + self.tmp)

	def stop(self):
		if self.proc:
			self.proc.terminate()
			self.proc.wait(timeout=20)
			self.proc = None

	def make_admin(self):
		code = (
			"from app.core.database import SessionLocal, Base, engine\n"
			"import app.main\n"
			"from app.core.security import hash_password\n"
			"from app.models.user import User\n"
			"db = SessionLocal()\n"
			"db.add(User(username='liveadmin', hashed_password=hash_password('live-admin-password-1'), role='admin', token_version=0, is_active=True))\n"
			"db.commit()\n"
		)
		subprocess.run([self.python, "-c", code], cwd=self.dir, env=self.env, check=True, capture_output=True)

	def sql(self, query, *args):
		with sqlite3.connect(self.db) as conn:
			return conn.execute(query, args).fetchall()

	def product(self, item_code):
		rows = self.sql(
			"select m.price, w.website_price, l.erp_price, m.quantity, l.sellable_qty, l.published, "
			"l.erp_price_valid_until "
			"from erp_product_links l join medicines m on m.id = l.medicine_id "
			"left join medicine_web w on w.medicine_id = m.id where l.erp_item_code = ?",
			item_code,
		)
		if not rows:
			return None
		keys = ("price", "website_price", "erp_price", "quantity", "sellable", "published", "valid_until")
		return dict(zip(keys, rows[0], strict=True))


def main():
	p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
	p.add_argument("--url", default="http://127.0.0.1:8000")
	p.add_argument("--site", required=True)
	p.add_argument("--actors", required=True, help="JSON with an Administrator API key/secret")
	p.add_argument("--bench", default="/home/frappe/frappe-bench")
	p.add_argument("--bench-cmd", default="bench", help="bench executable (run as the frappe user)")
	p.add_argument("--cloud-dir", required=True)
	p.add_argument("--cloud-python", required=True)
	args = p.parse_args()

	admin = json.load(open(args.actors))["Administrator"]
	erp = Erp(args.url, args.site, admin["key"], admin["secret"], args.bench, args.bench_cmd)
	secret = secrets.token_urlsafe(48)
	cloud = Cloud(args.cloud_dir, args.cloud_python, secret)
	check = Check()

	# ---------------------------------------------------------------- connect both systems
	subprocess.run(
		[
			"sudo",
			"-u",
			"frappe",
			"-H",
			"bash",
			"-c",
			f"cd {args.bench} && {args.bench_cmd} --site {args.site} set-config developer_mode 1",
		],
		check=True,
		capture_output=True,
	)
	branch = erp.call(
		"frappe.client.get_list",
		doctype="Branch",
		filters={"pharmacyos_storefront_code": "MAIN"},
		fields=["name", "pharmacyos_warehouse"],
	)[0]
	settings = erp.call("frappe.client.get", doctype="PharmacyOS Settings", name="PharmacyOS Settings")
	settings.update(
		{
			"enable_outbound_events": 1,
			"cloud_base_url": cloud.base,
			"outbound_endpoint": None,
			"outbound_secret": secret,
			"online_order_branch": branch["name"],
			"online_payment_mode": "Cash",
		}
	)
	erp.call("frappe.client.save", doc=settings)
	erp.job("pharmacyos_erp.integration.outbox.process_outbox")  # nothing listening yet: drain/mark only
	cloud.start()
	cloud.make_admin()
	try:
		run_flows(erp, cloud, check, branch, secret)
		run_pricing_flows(erp, cloud, check, branch, secret)
	finally:
		cloud.stop()
	failures = [r for r in check.results if not r[1]]
	print(json.dumps({"checks": len(check.results), "failures": len(failures), "cloud_log": cloud.tmp}))
	sys.exit(1 if failures else 0)


def run_flows(erp, cloud, check, branch, secret):
	outbox = "pharmacyos_erp.integration.outbox.process_outbox"
	pull = "pharmacyos_erp.integration.cloud.pull_orders"
	warehouse = branch["pharmacyos_warehouse"]
	company = erp.value("Warehouse", warehouse, "company")

	# ---------------------------------------------------------------- catalog and price A
	code = "LIVE-" + uuid.uuid4().hex[:6].upper()
	erp.call(
		"pharmacyos_erp.pharmacy.medicine.create_medicine",
		item_code=code,
		item_name=f"Live {code}",
		pharma_name_ar=f"دواء {code}",
		item_group="Products",
		stock_uom="Nos",
		standard_rate=1000,
		barcode=uuid.uuid4().hex[:12],
	)
	batch = erp.call(
		"pharmacyos_erp.pharmacy.receiving.create_receiving_batch",
		item_code=code,
		batch_id=f"LB-{code}",
		expiry_date="2099-12-31",
	)["name"]
	erp.call(
		"frappe.client.insert",
		doc={
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": company,
			"docstatus": 1,
			"items": [
				{
					"item_code": code,
					"qty": 5,
					"t_warehouse": warehouse,
					"basic_rate": 400,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
				}
			],
		},
	)
	erp.call("frappe.client.set_value", doctype="Item", name=code, fieldname="pharmacyos_publish", value=1)
	erp.job(outbox)
	product = cloud.product(code)
	check(
		"catalog: published medicine with ERP price A reaches the website",
		product and product["website_price"] == 1000,
		product,
	)
	check("catalog: sellable stock reaches the website", product and product["sellable"] == 5, product)

	# ---------------------------------------------------------------- N-05: price only
	price_name = erp.call(
		"frappe.client.get_value",
		doctype="Item Price",
		filters={"item_code": code, "price_list": "Standard Selling"},
		fieldname="name",
	)["name"]
	item_modified = erp.value("Item", code, "modified")
	erp.call(
		"frappe.client.set_value",
		doctype="Item Price",
		name=price_name,
		fieldname="price_list_rate",
		value=1250,
	)
	check("N-05: price change made no Item save", erp.value("Item", code, "modified") == item_modified)
	erp.job(outbox)
	product = cloud.product(code)
	check("N-05: price B reaches the website without an Item save", product["website_price"] == 1250, product)
	check(
		"N-05: price change leaves website stock untouched",
		product["sellable"] == 5 and product["quantity"] == 5,
		product,
	)

	# ---------------------------------------------------------------- outage and recovery
	cloud.stop()
	erp.call(
		"frappe.client.set_value",
		doctype="Item Price",
		name=price_name,
		fieldname="price_list_rate",
		value=1500,
	)
	erp.job(outbox)
	failed = erp.call(
		"frappe.client.get_list",
		doctype="PharmacyOS Sync Event",
		filters={"reference_name": code, "status": "Failed"},
		fields=["name", "last_error"],
	)
	check(
		"outage: event kept as unreachable while the Cloud is down",
		failed and all(f["last_error"].startswith("[unreachable]") for f in failed),
		failed,
	)
	status = erp.call("pharmacyos_erp.integration.outbox.get_sync_status")
	check("outage: System Status shows the Cloud offline", status["state"] == "offline", status)
	cloud.start()
	# back-off: a failed event is retried 2 minutes after its failure — really wait for it
	time.sleep(125)
	erp.job(outbox)
	product = cloud.product(code)
	check("recovery: the price changed during the outage arrives", product["website_price"] == 1500, product)
	status = erp.call("pharmacyos_erp.integration.outbox.get_sync_status")
	check("recovery: System Status back online", status["state"] == "online", status)

	# ---------------------------------------------------------------- duplicate and stale delivery
	sent = erp.call(
		"frappe.client.get_list",
		doctype="PharmacyOS Sync Event",
		filters={"reference_name": code, "event_type": "catalog.changed", "status": "Sent"},
		fields=["name", "payload"],
		order_by="creation asc",
	)
	oldest = sent[0]
	body = oldest["payload"].encode()
	signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
	again = requests.post(
		f"{cloud.base}/integrations/erp/events",
		data=body,
		headers={
			"Content-Type": "application/json",
			"X-PharmacyOS-Signature": signature,
			"X-PharmacyOS-Event-Id": oldest["name"],
		},
		timeout=10,
	)
	check(
		"duplicate: an already applied event is acknowledged, not re-applied",
		again.status_code == 200 and again.json().get("applied") is False,
		again.text[:200],
	)
	check("duplicate/stale: the newest price stays", cloud.product(code)["website_price"] == 1500)

	# ---------------------------------------------------------------- website order, lost response
	product_id = cloud.sql("select medicine_id from erp_product_links where erp_item_code = ?", code)[0][0]
	key = "live-" + uuid.uuid4().hex
	order = {
		"customer_name": "زبون تجريبي",
		"customer_phone": "07701112233",
		"delivery_address": "بغداد - الكرادة",
		"items": [{"product_id": product_id, "quantity": 2}],
	}
	first = requests.post(
		f"{cloud.base}/experience/public/orders", json=order, headers={"Idempotency-Key": key}, timeout=20
	)
	retry = requests.post(
		f"{cloud.base}/experience/public/orders", json=order, headers={"Idempotency-Key": key}, timeout=20
	)
	tracking = first.json().get("tracking_code") if first.status_code == 201 else None
	check("checkout: order placed", first.status_code == 201, first.text[:300])
	check(
		"checkout: retry with the same key returns the same order",
		retry.status_code == 200 and retry.json().get("tracking_code") == tracking,
		retry.text[:300],
	)
	check(
		"checkout: one order in the Cloud",
		cloud.sql("select count(*) from experience_orders where tracking_code = ?", tracking)[0][0] == 1,
	)
	erp.job(pull)
	erp.job(pull)  # a second pull must not import it again
	orders = erp.call(
		"frappe.client.get_list",
		doctype="Sales Order",
		filters={"pharmacyos_order_id": tracking},
		fields=["name", "docstatus"],
	)
	check(
		"import: one submitted Sales Order (reservation) in the ERP",
		len(orders) == 1 and orders[0]["docstatus"] == 1,
		orders,
	)
	erp.job(outbox)
	check(
		"import: website stock shows the reservation",
		cloud.product(code)["sellable"] == 3,
		cloud.product(code),
	)

	# ---------------------------------------------------------------- completion confirmed by the ERP
	browser = requests.Session()
	login = browser.post(
		f"{cloud.base}/auth/login",
		json={"username": "liveadmin", "password": "live-admin-password-1"},
		timeout=10,
	)
	csrf = login.json().get("csrf_token")
	order_id = cloud.sql("select id from experience_orders where tracking_code = ?", tracking)[0][0]
	for stage in ("reviewing", "completed"):
		r = browser.patch(
			f"{cloud.base}/experience/admin/orders/{order_id}",
			json={"status": stage, "status_note": ""},
			headers={"X-CSRF-Token": csrf},
			timeout=20,
		)
	check("completion: requested in the Cloud", r.status_code == 200, r.text[:300])
	erp.job(pull)
	invoices = erp.call(
		"frappe.client.get_list",
		doctype="Sales Invoice Item",
		parent="Sales Invoice",
		filters={"sales_order": orders[0]["name"], "docstatus": 1},
		fields=["parent", "batch_no", "qty"],
	)
	check(
		"completion: the ERP delivered and invoiced once", len({i["parent"] for i in invoices}) == 1, invoices
	)
	stage = cloud.sql("select status from experience_orders where id = ?", order_id)[0][0]
	check("completion: the Cloud shows completed only after the ERP confirmed", stage == "completed", stage)
	erp.job(outbox)
	check(
		"completion: website stock after delivery", cloud.product(code)["sellable"] == 3, cloud.product(code)
	)


def live_medicine(erp, company, warehouse, qty=5):
	code = "LIVE-" + uuid.uuid4().hex[:6].upper()
	erp.call(
		"pharmacyos_erp.pharmacy.medicine.create_medicine",
		item_code=code,
		item_name=f"Live {code}",
		pharma_name_ar=f"دواء {code}",
		item_group="Products",
		stock_uom="Nos",
		standard_rate=1000,
		barcode=uuid.uuid4().hex[:12],
	)
	batch = erp.call(
		"pharmacyos_erp.pharmacy.receiving.create_receiving_batch",
		item_code=code,
		batch_id=f"LB-{code}",
		expiry_date="2099-12-31",
	)["name"]
	erp.call(
		"frappe.client.insert",
		doc={
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": company,
			"docstatus": 1,
			"items": [
				{
					"item_code": code,
					"qty": qty,
					"t_warehouse": warehouse,
					"basic_rate": 400,
					"batch_no": batch,
					"use_serial_batch_fields": 1,
				}
			],
		},
	)
	erp.call("frappe.client.set_value", doctype="Item", name=code, fieldname="pharmacyos_publish", value=1)
	price = erp.call(
		"frappe.client.get_value",
		doctype="Item Price",
		filters={"item_code": code, "price_list": "Standard Selling", "customer": ["is", "not set"]},
		fieldname="name",
	)["name"]
	return code, price


def run_pricing_flows(erp, cloud, check, branch, secret):
	"""Round 3: only the public selling price valid today ever reaches the website."""
	import datetime

	outbox = "pharmacyos_erp.integration.outbox.process_outbox"
	pull = "pharmacyos_erp.integration.cloud.pull_orders"
	date_job = "pharmacyos_erp.integration.outbox.queue_price_validity_changes"
	warehouse = branch["pharmacyos_warehouse"]
	company = erp.value("Warehouse", warehouse, "company")
	today = erp.job("frappe.utils.nowdate").splitlines()[-1].strip().strip('"')  # the ERP's own date
	yesterday = str(datetime.date.fromisoformat(today) - datetime.timedelta(days=1))

	code, public = live_medicine(erp, company, warehouse)
	erp.job(outbox)
	check(
		"price: public price on the website",
		cloud.product(code)["website_price"] == 1000,
		cloud.product(code),
	)

	# customer-specific prices: cheaper, newer, edited, deleted — never public
	customer = erp.call("frappe.client.get_list", doctype="Customer", limit_page_length=1)[0]["name"]
	special = erp.call(
		"frappe.client.insert",
		doc={
			"doctype": "Item Price",
			"item_code": code,
			"price_list": "Standard Selling",
			"price_list_rate": 400,
			"customer": customer,
		},
	)["name"]
	erp.job(outbox)
	check(
		"price: a cheaper customer price is not published",
		cloud.product(code)["website_price"] == 1000,
		cloud.product(code),
	)
	erp.call(
		"frappe.client.set_value", doctype="Item Price", name=special, fieldname="price_list_rate", value=300
	)
	erp.job(outbox)
	check(
		"price: an edited customer price is not published",
		cloud.product(code)["website_price"] == 1000,
		cloud.product(code),
	)
	erp.call("frappe.client.delete", doctype="Item Price", name=special)
	erp.job(outbox)
	check(
		"price: deleting a customer price keeps the public one",
		cloud.product(code)["website_price"] == 1000,
		cloud.product(code),
	)

	# valid until today: the website knows the last valid day
	erp.call(
		"frappe.client.set_value", doctype="Item Price", name=public, fieldname="valid_upto", value=today
	)
	erp.job(outbox)
	product = cloud.product(code)
	check(
		"expiry: the validity reaches the website",
		product["valid_until"] == today and product["quantity"] == 5,
		product,
	)

	# midnight passes while the Cloud is down: the price row expires without any save
	cloud.stop()
	erp.job("frappe.db.set_value", ["Item Price", public, "valid_upto", yesterday])
	erp.job("frappe.db.set_default", ["pharmacyos_price_validity_date", yesterday])
	queued = erp.job(date_job)
	check(
		"expiry: the date job queued the item",
		(queued.strip().splitlines() or ["0"])[-1].strip() != "0",
		queued,
	)
	erp.job(outbox)  # Cloud unreachable: kept for retry
	cloud.start()
	time.sleep(125)  # back-off of a failed event
	erp.job(outbox)
	product = cloud.product(code)
	check(
		"expiry: after recovery the product is off sale",
		product["quantity"] == 0 and not product["published"],
		product,
	)
	product_id = cloud.sql("select medicine_id from erp_product_links where erp_item_code = ?", code)[0][0]
	order = {
		"customer_name": "زبون",
		"customer_phone": "07701112233",
		"delivery_address": "بغداد",
		"items": [{"product_id": product_id, "quantity": 1}],
	}
	refused = requests.post(f"{cloud.base}/experience/public/orders", json=order, timeout=20)
	check("expiry: checkout at the expired price is refused", refused.status_code == 409, refused.text[:200])
	again = erp.job(date_job)
	# (bench execute prints nothing for a falsy result: no output = 0 items queued)
	check(
		"expiry: running the date job again queues nothing",
		(again.strip().splitlines() or ["0"])[-1].strip() == "0",
		again,
	)

	# duplicate delivery of the expiry event
	sent = erp.call(
		"frappe.client.get_list",
		doctype="PharmacyOS Sync Event",
		filters={"reference_name": code, "event_type": "catalog.changed", "status": "Sent"},
		fields=["name", "payload"],
		order_by="creation desc",
		limit_page_length=1,
	)[0]
	body = sent["payload"].encode()
	duplicate = requests.post(
		f"{cloud.base}/integrations/erp/events",
		data=body,
		headers={
			"Content-Type": "application/json",
			"X-PharmacyOS-Signature": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
			"X-PharmacyOS-Event-Id": sent["name"],
		},
		timeout=10,
	)
	check(
		"expiry: a duplicate delivery is not re-applied",
		duplicate.json().get("applied") is False,
		duplicate.text[:200],
	)

	# replacement public price from today
	erp.call(
		"frappe.client.insert",
		doc={
			"doctype": "Item Price",
			"item_code": code,
			"price_list": "Standard Selling",
			"price_list_rate": 1800,
			"valid_from": today,
		},
	)
	erp.job(outbox)
	product = cloud.product(code)
	check(
		"replacement: back on sale at the new price with its stock",
		product["website_price"] == 1800 and product["quantity"] == 5 and product["published"],
		product,
	)
	placed = requests.post(f"{cloud.base}/experience/public/orders", json=order, timeout=20)
	check("replacement: checkout works again", placed.status_code == 201, placed.text[:200])

	# an order placed at a valid price whose price expires before the ERP imports it
	code2, public2 = live_medicine(erp, company, warehouse)
	erp.job(outbox)
	pid2 = cloud.sql("select medicine_id from erp_product_links where erp_item_code = ?", code2)[0][0]
	late = requests.post(
		f"{cloud.base}/experience/public/orders",
		json={**order, "items": [{"product_id": pid2, "quantity": 1}]},
		timeout=20,
	)
	tracking = late.json().get("tracking_code")
	check("import: order placed while the price was valid", late.status_code == 201, late.text[:200])
	erp.job("frappe.db.set_value", ["Item Price", public2, "valid_upto", yesterday])  # expired, no event yet
	erp.job(pull)
	imported = erp.call(
		"frappe.client.get_list", doctype="Sales Order", filters={"pharmacyos_order_id": tracking}
	)
	stage = cloud.sql("select status from experience_orders where tracking_code = ?", tracking)[0][0]
	check("import: the ERP refuses an item without a valid price", not imported, imported)
	check("import: the order goes to the pharmacist for review", stage == "reviewing", stage)


if __name__ == "__main__":
	main()
