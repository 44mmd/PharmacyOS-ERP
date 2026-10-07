"""A16: server updates through the control script (what the desktop app's 'Update now' runs).

Same build again → 'current'; a broken update (a migration that fails) → rolled back to the previous
release with the data as it was, maintenance off, server answering. QA-only; runs next to the test server.
"""

import json
import os
import shutil
import subprocess

from qalib import Results, Session
from qa_seed import OWNER

R = Results()
A = "A16 Update / rollback"
SRV = os.environ.get("SRV", "/var/tmp/srv.sh")
ROOT = "/var/tmp/wslroot"
BUNDLE = "/mnt/c/Program Files/PharmacyOS ERP/resources/server"


def srv(*args):
	out = subprocess.run([SRV, *args], capture_output=True, text=True, timeout=1800)
	result = next((json.loads(l.split(" ", 1)[1]) for l in reversed(out.stdout.splitlines()) if l.startswith("##PHARMACYOS-RESULT")), None)
	return out.returncode, result, out.stdout + out.stderr


def main():
	version, build = srv("version")[2].strip(), srv("build-id")[2].strip()
	code, res, log = srv("update", BUNDLE)
	R.add(A, "the same build again", "status current, nothing done", res, res == {"status": "current", "version": version})

	o = Session().login(*OWNER)
	if not o.get_list("Customer", filters=[["customer_name", "=", "QA BEFORE FAILED UPDATE"]], fields=["name"]):
		o.insert({"doctype": "Customer", "customer_name": "QA BEFORE FAILED UPDATE", "customer_type": "Individual"})
	sales = len(o.get_list("Sales Invoice", fields=["name"], limit=10000))

	# a broken release: same version, different code (so a new build), whose migration fails
	broken = ROOT + "/tmp/broken-bundle"
	shutil.rmtree(broken, ignore_errors=True)
	shutil.copytree(ROOT + BUNDLE, broken)
	app = broken + "/apps/pharmacyos_erp/pharmacyos_erp"
	with open(app + "/hooks.py", "a") as f:  # the new release cannot even load: migrate fails
		f.write("\nraise Exception('QA: a broken release that fails on purpose')\n")
	with open(broken + "/BUILD_ID", "w") as f:
		f.write("qa0broken0build0" + build[16:])
	os.system(f"chown -R 1000 {broken}")
	code, res, log = srv("update", "/tmp/broken-bundle")
	R.add(A, "a failing update rolls back", "status rolled_back, previous version", res, code != 0 and res and res.get("status") == "rolled_back" and res.get("version", "").startswith(version), evidence="\n".join(l for l in log.splitlines() if l.startswith("==") or "QA: a broken release" in l)[:900])
	R.add(A, "version after rollback", f"{version} / build {build[:12]}", (srv("version")[2].strip(), srv("build-id")[2].strip()[:12]), srv("version")[2].strip() == version)
	o = Session().login(*OWNER)
	R.add(A, "no pharmacy data lost", "marker customer and every sale still there", {"marker": bool(o.get_list("Customer", filters=[["customer_name", "=", "QA BEFORE FAILED UPDATE"]], fields=["name"])), "sales": len(o.get_list("Sales Invoice", fields=["name"], limit=10000)), "before": sales},
		bool(o.get_list("Customer", filters=[["customer_name", "=", "QA BEFORE FAILED UPDATE"]], fields=["name"])) and len(o.get_list("Sales Invoice", fields=["name"], limit=10000)) == sales)
	st, body = o.raw("GET", "/api/method/ping")
	R.add(A, "server answers, maintenance off after rollback", "ping 200 and a sale screen context", f"HTTP {st}", st == 200 and bool(o.call("pharmacyos_erp.pos.api.get_context")))
	shutil.rmtree(broken, ignore_errors=True)


if __name__ == "__main__":
	main()
