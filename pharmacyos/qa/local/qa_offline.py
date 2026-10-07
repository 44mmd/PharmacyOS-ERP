"""A18: no internet. Every outbound connection of the pharmacy server's processes is cut (its frappe user,
uid 1000 inside the test environment: direct connections and this sandbox's proxy), the Cloud integration
is switched on and pointed at an address that does not answer, and a whole counter shift runs: open,
scan, search, sell, receipt, return, report, close. Afterwards the Cloud events must be waiting in the
outbox (never lost, never blocking a sale) and the failure must be visible to the owner.

QA-only; needs root on the machine that hosts the test server (iptables owner match). The rules and the
settings are put back at the end whatever happens.
"""

import json
import os
import subprocess
import time

from qalib import Results, Session, rid
from qa_seed import OWNER, STAFF_PWD

R = Results()
A = "A18 Offline"
POS = "pharmacyos_erp.pos.api."
UID = "1000"  # the frappe user of the test server (gunicorn, workers, scheduler)
ROOT = "/var/tmp/wslroot"
PROXY_PORT = (os.environ.get("HTTPS_PROXY") or "http://127.0.0.1:0").rstrip("/").rsplit(":", 1)[-1]
RULES = [
	["OUTPUT", "-m", "owner", "--uid-owner", UID, "-p", "tcp", "-d", "127.0.0.1", "--dport", PROXY_PORT, "-j", "REJECT"],
	["OUTPUT", "-m", "owner", "--uid-owner", UID, "!", "-o", "lo", "-j", "REJECT"],
]
UNREACHABLE = "https://cloud-unreachable.pharmacyos-qa.example"
CHECK = "for u in https://github.com https://pypi.org; do curl -sS -m 8 -o /dev/null -w '%{http_code} ' $u 2>/dev/null || printf 'FAIL '; done"


def iptables(op, rule):
	return subprocess.run(["iptables", op, *rule], capture_output=True, text=True).returncode


def as_server_user(cmd):
	"""A shell command run inside the test server as its frappe user, with this sandbox's proxy settings."""
	env = " ".join(f"{k}={v}" for k, v in os.environ.items() if k.lower() in ("https_proxy", "http_proxy"))
	return subprocess.run(["chroot", ROOT, "su", "frappe", "-s", "/bin/bash", "-c", f"{env} {cmd}"], capture_output=True, text=True, timeout=60).stdout.strip()


def on_server(code):
	"""Python run on the server itself (as Administrator): reads what no client role can (outbox rows)."""
	with open(f"{ROOT}/tmp/qa_offline_read.py", "w") as f:
		f.write("import frappe, json\ndef run():\n" + "".join(f"\t{line}\n" for line in code.splitlines()))
	out = subprocess.run(["/var/tmp/py.sh", f"{ROOT}/tmp/qa_offline_read.py"], capture_output=True, text=True).stdout
	return json.loads(next(line[4:] for line in out.splitlines() if line.startswith("OUT=")))


def reachable(codes: str) -> bool:
	return any(c[:1] in "23" for c in codes.split())


def main():
	owner = Session("owner").login(*OWNER)
	before = as_server_user(CHECK)
	R.add(A, "the server reaches the internet before the test", "HTTP answers", before, reachable(before))
	keys = ("enable_outbound_events", "outbound_endpoint", "cloud_base_url", "outbound_timeout")
	saved = {k: owner.value("PharmacyOS Settings", "PharmacyOS Settings", k) for k in keys}
	start = on_server("print('OUT=' + json.dumps(str(frappe.utils.now_datetime())))")
	for rule in RULES:
		iptables("-I", rule)
	try:
		cut = as_server_user(CHECK)
		R.add(A, "internet cut for the pharmacy server (direct and through the proxy)", "no answer from any site", cut, not reachable(cut))
		owner.call("frappe.client.set_value", doctype="PharmacyOS Settings", name="PharmacyOS Settings",
			fieldname={"enable_outbound_events": 1, "outbound_endpoint": "", "cloud_base_url": UNREACHABLE, "outbound_timeout": 5, "outbound_secret": "qa-offline-secret"})
		cashier = Session("cashier").login("cashier@qa-pharmacy.test", STAFF_PWD)
		ctx = cashier.call(POS + "get_context")
		if ctx.get("shift"):
			profile = ctx["shift"]["pos_profile"]
		else:
			profile = next(p["name"] for p in ctx["profiles"])
			cashier.call("erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher", pos_profile=profile, company=ctx["profiles"][0]["company"], balance_details=json.dumps([{"mode_of_payment": "Cash", "opening_amount": 25000}]))
		R.add(A, "shift opens offline", "an open shift", profile, bool(cashier.call(POS + "get_context").get("shift")))
		t = time.perf_counter()
		scan = cashier.call(POS + "search_items", pos_profile=profile, search_term="6291100000011")
		scan_ms = round((time.perf_counter() - t) * 1000)
		R.add(A, "barcode scan offline", "Panadol found at once", {"items": [i["item_code"] for i in scan["items"]], "ms": scan_ms}, bool(scan["items"]) and scan_ms < 2000)
		ar = cashier.call(POS + "search_items", pos_profile=profile, search_term="بنادول")
		R.add(A, "Arabic search offline", "results", [i["item_code"] for i in ar["items"]][:3], bool(ar["items"]))
		t = time.perf_counter()
		sale = cashier.call(POS + "checkout", pos_profile=profile, items=[{"item_code": "QA-PAN500", "qty": 2}], payments=[{"mode_of_payment": "Cash", "amount": 10000}], request_id=rid())
		sale_ms = round((time.perf_counter() - t) * 1000)
		R.add(A, "cash sale offline, Cloud unreachable", "submitted without waiting for the Cloud", {"invoice": sale["name"], "total": sale["grand_total"], "change": sale.get("change_amount"), "ms": sale_ms}, bool(sale.get("name")) and sale_ms < 5000)
		st, html = cashier.raw("GET", f"/printview?doctype={sale['doctype'].replace(' ', '%20')}&name={sale['name']}&format=PharmacyOS%20Receipt&no_letterhead=1")
		R.add(A, "receipt offline", "receipt renders", f"HTTP {st}, {len(html)} bytes", st == 200 and ("Panadol" in html or "بنادول" in html))
		line = cashier.call(POS + "get_return_candidate", invoice=sale["name"])["lines"][0]
		ret = cashier.call(POS + "submit_return", invoice=sale["name"], lines=[{"row": line["row"], "qty": 1}], request_id=rid())
		R.add(A, "return offline", "return submitted", (ret.get("name"), ret.get("grand_total")), bool(ret.get("name")))
		rep = owner.call("pharmacyos_erp.pharmacy.reports.get_sales_report")
		R.add(A, "owner's report offline", "answers", {k: rep.get(k) for k in ("costs_visible",)} | {"has_sales": bool(rep.get("sales") or rep.get("summary"))}, isinstance(rep, dict))
		# the scheduler tries the Cloud every minute: wait for its attempt, which must fail quietly
		seen = {}
		for _ in range(30):
			seen = on_server(
				f"rows = frappe.get_all('PharmacyOS Sync Event', filters={{'creation': ['>=', '{start}']}}, fields=['status', 'attempts', 'last_error'])\n"
				"s = frappe.get_single('PharmacyOS Settings')\n"
				"print('OUT=' + json.dumps({'events': len(rows), 'sent': sum(r.status == 'Sent' for r in rows), 'tried': sum((r.attempts or 0) > 0 for r in rows), 'error': next((r.last_error for r in rows if r.last_error), None), 'cloud_last_error': s.cloud_last_error}))"
			)
			if seen["tried"] and seen["cloud_last_error"]:
				break
			time.sleep(10)
		R.add(A, "Cloud events wait in the outbox (not lost, not sent, not blocking)", "events queued, delivery tried and failed", seen, seen.get("events", 0) > 0 and seen.get("sent") == 0 and seen.get("tried", 0) > 0)
		R.add(A, "the Cloud failure is visible to the owner (PharmacyOS Settings → Cloud)", "last error recorded", (seen.get("cloud_last_error") or "")[:160], bool(seen.get("cloud_last_error")))
		summary = cashier.call(POS + "shift_summary")
		closed = cashier.call(POS + "close_shift", counted=[{"mode_of_payment": p["mode_of_payment"], "closing_amount": p["expected_amount"]} for p in summary["payments"]])
		R.add(A, "shift closes offline", "closing entry submitted", closed.get("name"), bool(closed.get("name")))
	finally:
		for rule in RULES:
			while iptables("-D", rule) == 0:
				pass
		owner.call("frappe.client.set_value", doctype="PharmacyOS Settings", name="PharmacyOS Settings",
			fieldname={k: (v if v is not None else ("" if k in ("outbound_endpoint", "cloud_base_url") else 0)) for k, v in saved.items()})
		# the offline test's own events are not real Cloud traffic
		on_server(f"frappe.db.delete('PharmacyOS Sync Event', {{'creation': ['>=', '{start}']}})\nprint('OUT=0')")
	back = as_server_user(CHECK)
	R.add(A, "internet back after the test", "HTTP answers again", back, reachable(back))


if __name__ == "__main__":
	main()
