"""Builds the per-workflow tables of docs/qa/local/FINAL_QA_REPORT.md from results.json (QA-only).

Every row is one check the QA scripts ran against the QA server: what was expected, what the server
actually did, PASS/FAIL, the screenshot that shows it (when there is one) and the automated test that
covers the same rule in the app's own suite. The prose around the tables is written by hand.

    python3 make_report.py > /tmp/tables.md
"""

import json
import os
import re
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.normpath(os.path.join(HERE, "../../docs/qa/local/screenshots"))

# area → (screenshots, automated tests in apps/pharmacyos_erp/pharmacyos_erp/tests)
AREAS = OrderedDict(
	[
		("A3 Fresh install", (["windows/01-setup-welcome", "windows/02-setup-pharmacy-details", "windows/03-setup-system-check-windows", "windows/04-setup-installing", "windows/06-setup-done", "05-login", "06-dashboard"], ["install-command.test.js", "setup.test.js", "Windows CI"])),
		("A5 QA pharmacy", (["06-dashboard", "21-medicines", "21b-medicine-form", "26-suppliers", "31-employees"], ["test_v1_certification (owner keeps the staff directory)", "test_local_journeys"])),
		("A6 Purchasing", (["27-purchase-order", "28-receiving", "29-purchase-return"], ["test_operations", "test_local_journeys", "test_v1_operations (supplier return)", "test_v1_certification (buyer receives with batch)"])),
		("A7 Inventory", (["22-inventory", "23-batches", "24-expiry"], ["test_pharmacy_core", "test_v1_operations (no re-dating)"])),
		("A8 POS shift", (["19-shift-open", "07-pos", "08-populated-cart", "08b-pos-arabic-search", "09-payment", "10-receipt-80mm-pos", "12-hold", "13-resume-list", "13b-resumed-cart", "14-sales-history"], ["test_web_pos", "test_pos_shift", "test_v1_certification (counter pricing)"])),
		("A9 Returns", (["16-return", "16b-return-receipt"], ["test_return_integrity_round4", "test_web_pos"])),
		("A10 Void", (["17-void-approval", "18-void-result", "15-sale-detail"], ["test_v1_operations (void, approver lock-out)"])),
		("A11 Shift close", (["20-shift-close", "20b-shift-closed"], ["test_pos_close_shift"])),
		("A12 Expired disposal", (["25-expired-disposal", "24-expiry"], ["test_v1_operations (disposal)"])),
		("A13 Reports", (["30-reports", "40-permission-denied"], ["test_v1_operations (report)", "test_cost_exposure"])),
		("A14 Permissions", (["32-roles-permissions", "40-permission-denied"], ["test_permissions_matrix", "test_cost_exposure", "test_credit_note_authority"])),
		("A15 Backup / restore", (["34-backups", "34b-backup-now-done", "35-restore-confirm", "35b-restore-running", "35c-restore-result"], ["test_backup"])),
		("A16 Update / rollback", (["36-update-offered", "36b-update-running", "36c-update-result", "39-about-version"], ["desktop: localserver.test.js (versions, builds)"])),
		("A18 Offline", ([], ["test_integration", "test_sync (outbox retry and back-off)"])),
		("A19 Barcode (simulated HID)", (["08-populated-cart", "08b-pos-arabic-search"], ["test_web_pos (scan)"])),
		("A20 Receipts (rendered)", (["10-receipt-80mm", "11-receipt-58mm", "10-receipt-80mm-pos", "16b-return-receipt"], ["test_experience (receipt)", "test_local_journeys"])),
		("A21 Security", (["40-permission-denied"], ["test_v1_certification", "test_remediation", "test_cost_exposure"])),
		("A22 Stress", ([], ["test_concurrency_round4", "test_return_concurrency"])),
	]
)


def esc(text) -> str:
	return str(text).replace("|", "\\|").replace("\n", " ")


def shots(names):
	have = [n for n in names if os.path.exists(os.path.join(SHOTS, n + ".png"))]
	return ", ".join(f"[{n}](screenshots/{n}.png)" for n in have) or "—"


def main():
	rows = json.load(open(os.path.join(HERE, "results.json")))
	by_area = OrderedDict()
	for r in rows:
		by_area.setdefault(r["area"], []).append(r)
	order = list(AREAS) + [a for a in by_area if a not in AREAS]
	total = sum(len(v) for v in by_area.values())
	passed = sum(1 for r in rows if r["result"] == "PASS")
	print(f"**{passed} / {total} checks PASS** ({total - passed} FAIL).\n")
	print("| Workflow | Checks | PASS | FAIL | Screenshots | Automated tests |")
	print("|---|---|---|---|---|---|")
	for area in order:
		if area not in by_area:
			continue
		rs = by_area[area]
		ok = sum(1 for r in rs if r["result"] == "PASS")
		names, tests = AREAS.get(area, ([], []))
		print(f"| {area} | {len(rs)} | {ok} | {len(rs) - ok} | {shots(names)} | {esc(', '.join(tests)) or '—'} |")
	for area in order:
		if area not in by_area:
			continue
		rs = sorted(by_area[area], key=lambda r: r["at"])
		print(f"\n### {area}\n")
		print("| # | Check | Expected | Actual (from the server) | Result | When |")
		print("|---|---|---|---|---|---|")
		for i, r in enumerate(rs, 1):
			actual = re.sub(r"\s+", " ", str(r["actual"]))[:260]
			print(f"| {i} | {esc(r['check'])} | {esc(r['expected'])} | {esc(actual)} | **{r['result']}** | {r['at'][11:16]} |")


if __name__ == "__main__":
	main()
