"""Builds the per-workflow tables of docs/qa/local/FINAL_QA_REPORT.md from results.json (QA-only).

Every row is one check the QA scripts ran against the QA server: what was expected, what the server
actually did, PASS/FAIL, and the automated test that covers the same rule in the app's own suite. The prose
around the tables is written by hand. Screenshots are not part of the report since 1.0.0-rc.3 (requirement
withdrawn by the product owner; docs/qa/local/screenshots holds historical rc.2 captures only).

    python3 make_report.py > /tmp/tables.md
"""

import json
import os
import re
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))

# area → automated tests (apps/pharmacyos_erp/pharmacyos_erp/tests, desktop/test) covering the same rules
AREAS = OrderedDict(
	[
		("A3 Fresh install", ["install-command.test.js", "setup.test.js", "Windows CI", "test_terminology (served Arabic)"]),
		("A5 QA pharmacy", ["test_v1_certification (owner keeps the staff directory)", "test_local_journeys"]),
		("A6 Purchasing", ["test_operations", "test_local_journeys", "test_v1_operations (supplier return)", "test_v1_certification (buyer receives with batch)"]),
		("A7 Inventory", ["test_pharmacy_core", "test_v1_operations (no re-dating)"]),
		("A8 POS shift", ["test_web_pos", "test_pos_shift", "test_v1_certification (counter pricing)"]),
		("A9 Returns", ["test_return_integrity_round4", "test_web_pos"]),
		("A10 Void", ["test_v1_operations (void, approver lock-out)"]),
		("A11 Shift close", ["test_pos_close_shift"]),
		("A12 Expired disposal", ["test_v1_operations (disposal)"]),
		("A13 Reports", ["test_v1_operations (report)", "test_cost_exposure"]),
		("A14 Permissions", ["test_permissions_matrix", "test_cost_exposure", "test_credit_note_authority"]),
		("A15 Backup / restore", ["test_backup"]),
		("A16 Update / rollback", ["desktop: localserver.test.js (versions, builds)", "test_terminology"]),
		("A18 Offline", ["test_integration", "test_sync (outbox retry and back-off)"]),
		("A19 Barcode (simulated HID)", ["test_web_pos (scan)"]),
		("A20 Receipts (rendered)", ["test_experience (receipt)", "test_local_journeys"]),
		("A21 Security", ["test_v1_certification", "test_remediation", "test_cost_exposure"]),
		("A22 Stress", ["test_concurrency_round4", "test_return_concurrency"]),
	]
)


def esc(text) -> str:
	return str(text).replace("|", "\\|").replace("\n", " ")


def main():
	rows = json.load(open(os.path.join(HERE, "results.json")))
	by_area = OrderedDict()
	for r in rows:
		by_area.setdefault(r["area"], []).append(r)
	order = list(AREAS) + [a for a in by_area if a not in AREAS]
	total = sum(len(v) for v in by_area.values())
	passed = sum(1 for r in rows if r["result"] == "PASS")
	print(f"**{passed} / {total} checks PASS** ({total - passed} FAIL).\n")
	print("| Workflow | Checks | PASS | FAIL | Automated tests |")
	print("|---|---|---|---|---|")
	for area in order:
		if area not in by_area:
			continue
		rs = by_area[area]
		ok = sum(1 for r in rs if r["result"] == "PASS")
		tests = AREAS.get(area, [])
		print(f"| {area} | {len(rs)} | {ok} | {len(rs) - ok} | {esc(', '.join(tests)) or '—'} |")
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
