"""A16: server updates through the control script (what the desktop app's 'Update now' runs).

Same build again → 'current'; a broken update (a migration that fails) → rolled back to the previous
release with the data as it was, maintenance off, server answering. QA-only; runs next to the test server.

The real update from the previous release (1.0.0-rc.2 → 1.0.0-rc.3), on a server installed fresh from the
previous release's bundle and given QA data by qa_seed.py and qa_ops.py (sales, returns, a void, a closed
shift; their rows go to a separate QA_OUT file):

    python3 qa_update.py before                # on the previous release: what must survive (marker, sales,
                                               # version, the counter's Arabic before the update)
    python3 qa_update.py upgrade <new bundle>  # what the pharmacy gets: the new installer puts its server
                                               # bundle in resources/server (NSIS upgrade), then the app's
                                               # Update now runs `pharmacyos-server update <that folder>`;
                                               # result, version/build, data kept, the counter's Arabic
    python3 qa_update.py                       # then: the same build again, a failing update rolls back
"""

import glob
import json
import os
import shutil
import subprocess
import sys

from qalib import Results, Session, counter_shift_arabic, desk_shift_arabic, desk_shift_arabic_ok, shift_arabic_ok
from qa_seed import OWNER, STAFF_PWD

R = Results()
A = "A16 Update / rollback"
SRV = os.environ.get("SRV", "/var/tmp/srv.sh")
ROOT = "/var/tmp/wslroot"
BUNDLE = "/mnt/c/Program Files/PharmacyOS ERP/resources/server"


def srv(*args):
	out = subprocess.run([SRV, *args], capture_output=True, text=True, timeout=1800)
	result = next((json.loads(l.split(" ", 1)[1]) for l in reversed(out.stdout.splitlines()) if l.startswith("##PHARMACYOS-RESULT")), None)
	return out.returncode, result, out.stdout + out.stderr


STATE = os.environ.get("QA_UPDATE_STATE", "/var/tmp/qa-update-before.json")
MARKER = "QA BEFORE GUIDED UPDATE"


def sales_count(o):
	return len(o.get_list("Sales Invoice", fields=["name"], limit=10000))


def has_marker(o):
	return bool(o.get_list("Customer", filters=[["customer_name", "=", MARKER]], fields=["name"]))


def before():
	"""On the server of the previous release, before the desktop app's Update now (no results row: the state
	is compared after the update)."""
	o = Session().login(*OWNER)
	if not has_marker(o):
		o.insert({"doctype": "Customer", "customer_name": MARKER, "customer_type": "Individual"})
	counter = counter_shift_arabic(o)
	state = {"version": srv("version")[2].strip(), "build": srv("build-id")[2].strip(), "sales": sales_count(o),
		"old_word_on_counter": counter["old_word_left"], "new_word_on_counter": shift_arabic_ok(counter),
		"old_word_in_desk": len(desk_shift_arabic(o)["old_word_left"])}
	with open(STATE, "w") as f:
		json.dump(state, f)
	print(state)


def upgrade(new_bundle):
	"""The new installer's server bundle replaces the installed app's resources/server (NSIS upgrade over the
	old version), then Update now: `pharmacyos-server update "<resources/server>"`, exactly as the app runs it."""
	if not os.path.isfile(os.path.join(new_bundle, "VERSION")):
		sys.exit(f"not a server bundle: {new_bundle}")
	shutil.rmtree(ROOT + BUNDLE)
	shutil.copytree(new_bundle, ROOT + BUNDLE, symlinks=True)
	code, res, log = srv("update", BUNDLE)
	print(f"update exit {code}: {res}")
	guided()


def guided():
	"""After Update now: the update the pharmacy really gets, from the previous release."""
	b = json.load(open(STATE))
	bundle_version = open(ROOT + BUNDLE + "/VERSION").read().strip()
	bundle_build = open(ROOT + BUNDLE + "/BUILD_ID").read().strip()
	logs = sorted(glob.glob(ROOT + "/var/log/pharmacyos/update-*.log"), key=os.path.getmtime)
	text = open(logs[-1]).read() if logs else ""
	res = next((json.loads(l.split(" ", 1)[1]) for l in reversed(text.splitlines()) if l.startswith("##PHARMACYOS-RESULT")), None)
	R.add(A, f"the update from the previous release ({b['version']} → {bundle_version}): new installer's bundle, then Update now (pharmacyos-server update)", f"status updated, previous {b['version']}, safety backup taken",
		res, bool(res) and res.get("status") == "updated" and res.get("previous") == b["version"] and res.get("version") == bundle_version and bool(res.get("backup")),
		evidence="\n".join(l for l in text.splitlines() if l.startswith("==") or "MO file" in l)[:900])
	version, build = srv("version")[2].strip(), srv("build-id")[2].strip()
	R.add(A, "version and build after the update", f"{bundle_version} / build {bundle_build[:12]} (the bundle the app carries)", (version, build[:12]), version == bundle_version and build == bundle_build)
	o = Session().login(*OWNER)
	kept = {"marker": has_marker(o), "sales": sales_count(o), "before": b["sales"]}
	R.add(A, "pharmacy data kept across the update", "marker customer and every sale still there", kept, kept["marker"] and kept["sales"] == b["sales"])
	after = counter_shift_arabic(o)
	R.add(A, "the counter's Arabic after the update: a shift is «شِفت» (recompiled during the update's migrate)", "before: the older word; after: الشِفت · فتح الشِفت · إغلاق الشِفت …, no older word left",
		{"before": {"version": b["version"], "older_word_on_counter": b["old_word_on_counter"]}, "after": after}, b["old_word_on_counter"] > 0 and not b["new_word_on_counter"] and shift_arabic_ok(after))
	desk = desk_shift_arabic(o)
	R.add(A, "the desk's Arabic after the update (Pharmacy Report, sidebar): shifts are «شِفتات»", "before: strings with the older word; after: الشِفتات · شِفتات نقطة البيع …, none left",
		{"before": {"strings_with_older_word": b["old_word_in_desk"]}, "after": desk}, b["old_word_in_desk"] > 0 and desk_shift_arabic_ok(desk))
	# a message the server itself raises (Python, from the compiled translations): the cashier's shift was
	# closed on the previous release (qa_ops.py A11), so there is none to summarise
	cashier = Session("cashier").login("cashier@qa-pharmacy.test", STAFF_PWD)
	R.refused(A, "after the update the server's own messages use «شِفت» (cashier without an open shift)", "refused: ليس لديك شِفت مفتوح.",
		lambda: cashier.call("pharmacyos_erp.pos.api.shift_summary"), must_contain="ليس لديك شِفت مفتوح.")


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
	step = sys.argv[1] if len(sys.argv) > 1 else ""
	if step == "upgrade":
		upgrade(sys.argv[2])
	else:
		{"before": before, "guided": guided}.get(step, main)()
