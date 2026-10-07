"""A3 + A20: the state a fresh install leaves (before any QA data), and the receipts as printed pages.

A3 — run right after install-server.sh finished on an empty machine and BEFORE qa_seed.py: production
mode, no sample data, the owner and the first counter, security settings, services.
A20 — run after qa_ops.py: the PharmacyOS Receipt for a real counter sale at 80 mm and at 58 mm (Arabic
and English names, discount, cash and change) and for a return. Rendered pages only: no printer is
attached here, so nothing about physical printing is claimed.

    python3 qa_install.py fresh      # A3
    python3 qa_install.py receipts   # A20
"""

import json
import re
import subprocess
import sys

from qalib import Results, Session
from qa_seed import OWNER

R = Results()
ROOT = "/var/tmp/wslroot"


def on_server(code):
	"""Python on the server as Administrator: reads site configuration no web role sees."""
	with open(f"{ROOT}/tmp/qa_install_read.py", "w") as f:
		f.write("import frappe, json\ndef run():\n" + "".join(f"\t{line}\n" for line in code.splitlines()))
	out = subprocess.run(["/var/tmp/py.sh", f"{ROOT}/tmp/qa_install_read.py"], capture_output=True, text=True).stdout
	return json.loads(next(line[4:] for line in out.splitlines() if line.startswith("OUT=")))


def fresh():
	A = "A3 Fresh install"
	s = on_server(
		"c = frappe.get_conf()\n"
		"ss = frappe.get_single('System Settings')\n"
		"out = {\n"
		" 'developer_mode': c.get('developer_mode'), 'allow_tests': c.get('allow_tests'),\n"
		" 'items': frappe.db.count('Item'), 'customers': frappe.db.count('Customer', {'name': ['!=', 'Walk-in Customer']}),\n"
		" 'invoices': frappe.db.count('Sales Invoice'), 'suppliers': frappe.db.count('Supplier'),\n"
		" 'users': frappe.get_all('User', filters={'user_type': 'System User', 'name': ['not in', ['Administrator']]}, pluck='name'),\n"
		" 'owner_roles': sorted(frappe.get_roles('owner@qa-pharmacy.test')),\n"
		" 'companies': frappe.get_all('Company', pluck='name'),\n"
		" 'pos_profiles': frappe.get_all('POS Profile', fields=['name', 'warehouse', 'print_format', 'selling_price_list']),\n"
		" 'branches': frappe.get_all('Branch', fields=['name', 'pharmacyos_warehouse']),\n"
		" 'enable_password_policy': ss.enable_password_policy, 'minimum_password_score': ss.minimum_password_score,\n"
		" 'disable_signup': frappe.db.get_single_value('Website Settings', 'disable_signup'),\n"
		" 'scheduler': not frappe.utils.scheduler.is_scheduler_disabled(verbose=False),\n"
		" 'time_zone': ss.time_zone, 'language': ss.language, 'currency': frappe.db.get_default('currency'),\n"
		" 'setup_complete': frappe.is_setup_complete(),\n"
		" 'jobs_in_future': frappe.db.sql_list(\"select method from `tabScheduled Job Type` where coalesce(last_execution, creation) > %s\", (frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=5),)),\n"
		" 'max_counter_discount': frappe.db.sql(\"select value from tabSingles where doctype='PharmacyOS Settings' and field='max_counter_discount'\"),\n"
		"}\n"
		"print('OUT=' + json.dumps(out, default=str))"
	)
	R.add(A, "production mode (no developer mode, no test runner)", "developer_mode and allow_tests off", {k: s[k] for k in ("developer_mode", "allow_tests")}, not s["developer_mode"] and not s["allow_tests"])
	R.add(A, "no sample data", "0 items, customers (besides walk-in), invoices, suppliers", {k: s[k] for k in ("items", "customers", "invoices", "suppliers")}, s["items"] == 0 and s["invoices"] == 0 and s["suppliers"] == 0)
	R.add(A, "one owner account, nobody else", "owner@… only", s["users"], s["users"] == ["owner@qa-pharmacy.test"])
	R.add(A, "the owner holds the Pharmacy Owner profile", "Pharmacy Owner + System Manager", s["owner_roles"], "Pharmacy Owner" in s["owner_roles"] and "System Manager" in s["owner_roles"])
	R.add(A, "company, branch and warehouse created by the setup", "one company, one branch with its warehouse", {"companies": s["companies"], "branches": s["branches"]}, len(s["companies"]) == 1 and s["branches"] and s["branches"][0]["pharmacyos_warehouse"])
	R.add(A, "first counter ready (Main Counter, cash, receipt format)", "POS Profile with warehouse, price list, PharmacyOS Receipt", s["pos_profiles"], any(p["name"] == "Main Counter" and p["warehouse"] and p["print_format"] == "PharmacyOS Receipt" for p in s["pos_profiles"]))
	R.add(A, "password policy on, sign-up off", "policy enabled, sign-up disabled", {k: s[k] for k in ("enable_password_policy", "minimum_password_score", "disable_signup")}, bool(s["enable_password_policy"]) and bool(s["disable_signup"]))
	R.add(A, "scheduler on (hourly backups)", "enabled", s["scheduler"], bool(s["scheduler"]))
	R.add(A, "no scheduled job waits in the future (time zone set at first run)", "none", s["jobs_in_future"][:5] or "none", not s["jobs_in_future"])
	R.add(A, "Arabic, Iraq time zone, IQD", "ar / Asia/Baghdad / IQD", {k: s[k] for k in ("language", "time_zone", "currency")}, s["language"] == "ar" and s["time_zone"] == "Asia/Baghdad" and s["currency"] == "IQD")
	R.add(A, "counter discount ceiling stored (10 %)", "10", s["max_counter_discount"], bool(s["max_counter_discount"]) and float(s["max_counter_discount"][0][0]) == 10)
	owner = Session("owner").login(*OWNER)
	st, html = owner.raw("GET", "/app/pharmacy-dashboard")
	R.add(A, "the owner signs in and lands on the Pharmacy Dashboard", "HTTP 200", f"HTTP {st}", st == 200)
	g = Session("guest")
	st, _ = g.raw("POST", "/api/method/frappe.core.doctype.user.user.sign_up", {"email": "x@example.com", "full_name": "x", "redirect_to": ""})
	R.add(A, "self sign-up refused", "refused (sign-up disabled)", f"HTTP {st}", st in (403, 417, 500) or st == 200 and "disabled" in _.lower())
	services = subprocess.run(["chroot", ROOT, "supervisorctl", "status"], capture_output=True, text=True).stdout
	running = re.findall(r"^(\S+)\s+RUNNING", services, re.M)
	R.add(A, "all server programs running", "web, socket.io, scheduler, 2 workers, 2 Redis", running, len(running) >= 7)


def receipts():
	A = "A20 Receipts (rendered)"
	owner = Session("owner").login(*OWNER)
	sale = None
	# a counter sale with a line discount, paid in cash with change (qa_ops T2)
	for row in owner.get_list("Sales Invoice", filters=[["docstatus", "=", 1], ["is_return", "=", 0], ["is_pos", "=", 1], ["change_amount", ">", 0]], fields=["name"], order_by="creation asc", limit=50):
		doc = owner.get_doc("Sales Invoice", row["name"])
		if any(i.get("discount_percentage") for i in doc["items"]):
			sale = doc
			break
	ret = owner.get_list("Sales Invoice", filters=[["docstatus", "=", 1], ["is_return", "=", 1]], fields=["name"], order_by="creation desc", limit=1)

	def render(name):
		st, html = owner.raw("GET", f"/printview?doctype=Sales%20Invoice&name={name}&format=PharmacyOS%20Receipt&no_letterhead=1")
		return st, html

	def setw(w):
		owner.call("frappe.client.set_value", doctype="PharmacyOS Settings", name="PharmacyOS Settings", fieldname="receipt_paper_width", value=w)

	if not sale:
		R.add(A, "a counter sale with a discount and change exists", "found", None, False)
		return
	st, html = render(sale["name"])
	names = [(i["item_name"], i.get("pharma_name_ar")) for i in sale["items"]]
	R.add(A, "80 mm receipt of a counter sale", "HTTP 200, 80 mm layout (no 58 mm rule)", f"HTTP {st}, {len(html)} bytes, 58mm rule: {'58mm' in html}", st == 200 and "max-width: 58mm" not in html)
	R.add(A, "Arabic and English medicine names on the receipt", "both names printed", names[:3], st == 200 and all(n[0] in html for n in names))
	R.add(A, "discount, cash received and change printed", "line discount and change figures present", {"invoice": sale["name"], "line_discounts": [i.get("discount_percentage") for i in sale["items"]], "paid": sale.get("paid_amount"), "change": sale.get("change_amount")},
		st == 200 and f"{int(sale['change_amount']):,}" in html)
	setw("58mm")
	try:
		st58, html58 = render(sale["name"])
		R.add(A, "58 mm receipt (PharmacyOS Settings → Receipt Paper Width)", "narrow layout: max-width 58mm, @page 58mm", f"HTTP {st58}, @page 58mm: {'size: 58mm' in html58}", st58 == 200 and "max-width: 58mm" in html58 and "size: 58mm" in html58)
	finally:
		setw("80mm")
	if ret:
		st, html = render(ret[0]["name"])
		R.add(A, "return (refund) receipt", "renders, marked as a return", f"HTTP {st}, {len(html)} bytes", st == 200)


if __name__ == "__main__":
	{"fresh": fresh, "receipts": receipts}[sys.argv[1]]()
