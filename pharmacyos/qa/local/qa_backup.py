"""A15: backup → change → restore, through the server control script (what the desktop app runs).

QA BEFORE BACKUP must exist after the restore, QA AFTER BACKUP must be gone, a safety backup must have been
taken, the server must answer again, and stock must still equal the stock ledger. QA-only.
Run on the machine that hosts the test server: SRV=/var/tmp/srv.sh python3 qa_backup.py
"""

import json
import os
import subprocess

from qalib import Results, Session
from qa_seed import OWNER

R = Results()
A = "A15 Backup / restore"
SRV = os.environ.get("SRV", "/var/tmp/srv.sh")


def srv(*args):
	out = subprocess.run([SRV, *args], capture_output=True, text=True, timeout=1800)
	result = next((json.loads(l.split(" ", 1)[1]) for l in reversed(out.stdout.splitlines()) if l.startswith("##PHARMACYOS-RESULT")), None)
	return out.returncode, result, out.stdout + out.stderr


def customer(s, name):
	if not s.get_list("Customer", filters=[["customer_name", "=", name]], fields=["name"]):
		s.insert({"doctype": "Customer", "customer_name": name, "customer_type": "Individual"})


def has(s, name):
	return bool(s.get_list("Customer", filters=[["customer_name", "=", name]], fields=["name"]))


def consistent(s):
	bad = []
	for b in s.get_list("Bin", filters=[["item_code", "like", "QA-%"]], fields=["item_code", "warehouse", "actual_qty"], limit=1000):
		rows = s.get_list("Stock Ledger Entry", filters=[["item_code", "=", b["item_code"]], ["warehouse", "=", b["warehouse"]], ["is_cancelled", "=", 0]], fields=[{"SUM": "actual_qty", "as": "q"}])
		if abs((rows[0]["q"] or 0) - b["actual_qty"]) > 1e-6:
			bad.append(b["item_code"])
	return bad


def main():
	o = Session().login(*OWNER)
	customer(o, "QA BEFORE BACKUP")
	sales_before = len(o.get_list("Sales Invoice", fields=["name"], limit=5000))
	code, res, log = srv("backup")
	R.add(A, "back up now (database + files, verified)", "status ok with a backup folder", res, code == 0 and res and res.get("status") == "ok")
	folder = res["folder"]
	customer(o, "QA AFTER BACKUP")
	R.add(A, "change after the backup", "QA AFTER BACKUP exists before the restore", has(o, "QA AFTER BACKUP"), has(o, "QA AFTER BACKUP"))
	backups_before = set(os.listdir(os.path.dirname(os.path.dirname("/var/tmp/wslroot" + folder))))
	code, res, log = srv("restore", folder)
	R.add(A, "restore that backup", "status restored", res, code == 0 and res and res.get("status") == "restored", evidence=log[-600:])
	safety = [l.split("safety backup:", 1)[1].strip() for l in log.splitlines() if "safety backup:" in l]
	R.add(A, "a safety backup was taken first", "safety backup folder printed and present", safety, bool(safety) and os.path.exists("/var/tmp/wslroot" + safety[0] + "/metadata.json"))
	o = Session().login(*OWNER)  # sessions belong to the restored database
	R.add(A, "server answers again after the restore", "sign-in works", o.user, True)
	R.add(A, "QA BEFORE BACKUP is there", "present", has(o, "QA BEFORE BACKUP"), has(o, "QA BEFORE BACKUP"))
	R.add(A, "QA AFTER BACKUP is gone", "absent", has(o, "QA AFTER BACKUP"), not has(o, "QA AFTER BACKUP"))
	R.add(A, "transactions consistent", "the same sales as at backup time", {"sales": len(o.get_list("Sales Invoice", fields=["name"], limit=5000)), "at_backup": sales_before}, len(o.get_list("Sales Invoice", fields=["name"], limit=5000)) == sales_before)
	bad = consistent(o)
	R.add(A, "inventory consistent (Bin = stock ledger)", "no mismatch", bad or "all consistent", not bad)

	# a folder that is not one of this server's backups is refused (planted / injected names)
	planted = "/mnt/c/ProgramData/PharmacyOS/Backups/2099-01-01/x';touch /tmp/pwned;'"
	os.makedirs("/var/tmp/wslroot" + planted, exist_ok=True)
	open("/var/tmp/wslroot" + planted + "/metadata.json", "w").write('{"site": "pharmacy.local", "files": []}')
	code, res, log = srv("restore", planted)
	R.add(A, "a planted backup folder name is refused", "invalid_folder, nothing run", res, code != 0 and res and res.get("status") == "invalid_folder" and not os.path.exists("/var/tmp/wslroot/tmp/pwned"))
	listed = srv("list-backups")[2]
	R.add(A, "planted folders are not listed", "2099-01-01 not in the Backups list", "2099-01-01" in listed, "2099-01-01" not in listed)
	import shutil

	shutil.rmtree("/var/tmp/wslroot/mnt/c/ProgramData/PharmacyOS/Backups/2099-01-01", ignore_errors=True)

	# a backup whose checksum does not match is refused before anything changes
	import glob

	victim = sorted(glob.glob("/var/tmp/wslroot/mnt/c/ProgramData/PharmacyOS/Backups/*/*/metadata.json"))[0].rsplit("/", 1)[0]
	tampered = victim + "-99"
	shutil.copytree(victim, tampered)
	with open(tampered + "/database.sql.gz", "ab") as f:
		f.write(b"tampered")
	customer(o, "QA BEFORE TAMPER TEST")
	code, res, log = srv("restore", tampered[len("/var/tmp/wslroot"):])
	o = Session().login(*OWNER)
	R.add(A, "a tampered backup is refused, data unchanged", "failed_unchanged (exit 3); current data kept", res, res and res.get("status") in ("failed_unchanged", "invalid_folder") and has(o, "QA BEFORE TAMPER TEST"))
	shutil.rmtree(tampered, ignore_errors=True)


if __name__ == "__main__":
	main()


def broken_import():
	"""A backup whose checksums are right but whose database dump cannot be imported: the restore fails
	after the database was dropped — the safety backup must be put back automatically (failed_reverted)."""
	import datetime
	import gzip
	import hashlib

	o = Session().login(*OWNER)
	customer(o, "QA BEFORE BROKEN RESTORE")
	day = datetime.date.today().isoformat()
	rel = f"/mnt/c/ProgramData/PharmacyOS/Backups/{day}/23-58"
	folder = "/var/tmp/wslroot" + rel
	os.makedirs(folder, exist_ok=True)
	with gzip.open(folder + "/database.sql.gz", "wb") as f:
		f.write(b"CREATE TABLE `tabBroken` (x int);\nTHIS IS NOT SQL AT ALL;\n")
	sha = hashlib.sha256(open(folder + "/database.sql.gz", "rb").read()).hexdigest()
	json.dump({"format": "pharmacyos-backup/1", "site": "pharmacy.local", "created_at": f"{day}T23:58:00", "app_versions": {}, "encrypted": False,
		"files": [{"name": "database.sql.gz", "size": os.path.getsize(folder + "/database.sql.gz"), "sha256": sha}]}, open(folder + "/metadata.json", "w"))
	os.system(f"chown -R 1000 '{folder}'")
	code, res, log = srv("restore", rel)
	o = Session().login(*OWNER)
	R.add(A, "a restore that fails mid-way puts the safety backup back", "failed_reverted; data as before; server answers", {"result": res, "marker_kept": has(o, "QA BEFORE BROKEN RESTORE")},
		res and res.get("status") == "failed_reverted" and has(o, "QA BEFORE BROKEN RESTORE"), evidence=log[-800:])
	import shutil

	shutil.rmtree(folder, ignore_errors=True)


def one_at_a_time():
	"""A second server operation while one runs (another window, a reload) is refused as busy, never run
	alongside. The long operation is an update whose new release fails (backup, failed migrate, rollback);
	a second update is asked for meanwhile."""
	import shutil
	import time

	root, bundle = "/var/tmp/wslroot", "/mnt/c/Program Files/PharmacyOS ERP/resources/server"
	broken = root + "/tmp/busy-bundle"
	shutil.rmtree(broken, ignore_errors=True)
	shutil.copytree(root + bundle, broken)
	with open(broken + "/apps/pharmacyos_erp/pharmacyos_erp/hooks.py", "a") as f:
		f.write("\nraise Exception('QA: a broken release that fails on purpose')\n")
	with open(broken + "/BUILD_ID", "w") as f:
		f.write("qa0busy0" + "0" * 56)
	os.system(f"chown -R 1000 {broken}")
	first = subprocess.Popen([SRV, "update", "/tmp/busy-bundle"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
	time.sleep(4)
	code, res, _log = srv("update", "/tmp/busy-bundle")
	out, _ = first.communicate(timeout=1800)
	first_res = next((json.loads(l.split(" ", 1)[1]) for l in reversed(out.splitlines()) if l.startswith("##PHARMACYOS-RESULT")), None)
	R.add("A16 Update / rollback", "one server operation at a time (a second update while one runs)", "the second is refused as busy (exit 75); the first finishes (rolled back)",
		{"second_exit": code, "second": res, "first": first_res}, code == 75 and (res or {}).get("status") == "busy" and (first_res or {}).get("status") == "rolled_back")
	shutil.rmtree(broken, ignore_errors=True)


if __name__ == "__main__" and os.environ.get("QA_BROKEN"):
	broken_import()
if __name__ == "__main__" and os.environ.get("QA_BUSY"):
	one_at_a_time()
