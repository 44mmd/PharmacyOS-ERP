# PharmacyOS Local ERP 1.0.0-rc.3 — release checklist

Status of every item on the final commit of this pass. **Done** = verified in this pass (evidence named).
**Owner** = needs a person, a device or a decision outside this repository. Nothing here was published,
signed, merged or deployed.

1.0.0-rc.3 changes the Arabic terminology only (a cash/POS shift is «شِفت», plural «شِفتات», masculine —
`docs/RELEASE.md` → Release notes); it was certified again from a fresh install, with a real update from
1.0.0-rc.2. Screenshots are no longer part of the release evidence (requirement withdrawn by the product
owner); `docs/qa/local/screenshots/` keeps the historical 1.0.0-rc.2 captures only.

## Software (done)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Version is the same everywhere: desktop `1.0.0-rc.3`, server app `1.0.0rc3`, bundle `VERSION` `1.0.0rc3` | Done | `desktop/package.json` (+ lock), `pharmacyos_erp/__init__.py`, bundle `VERSION`; A16 "version and build after the update" |
| 2 | Pinned upstream: Frappe 16.36.1 / ERPNext 16.37.0 by tag **and** commit, verified at install | Done | `deploy/server/install-server.sh`, `dev/setup-dev-bench.sh` |
| 3 | Full app test suite green on the pinned versions | Done | `bench run-tests --app pharmacyos_erp` — **265 tests, OK** (462 s, host development bench, Frappe 16.36.1 / ERPNext 16.37.0) (FINAL_QA_REPORT § Automated tests) |
| 4 | Desktop unit tests green | Done | `npm test` (29/29), also on Windows CI |
| 5 | Windows installer built, installed silently, first launch, uninstall keeps data | Done | Windows CI push run of the release commit (installer file name, size, SHA-256 and signing printed in the job log and the run summary) |
| 6 | Setup scripts run on Windows PowerShell 5.1 (BOM, helpers, data-folder lock, planted link removed) | Done | Windows CI step "Setup scripts work in Windows PowerShell 5.1" |
| 7 | Fresh server install from nothing in the exact Ubuntu 24.04 WSL image (rc.3 bundle) | Done | QA server (chroot of the WSL rootfs) — FINAL_QA_REPORT § A3 |
| 8 | Production mode on the installed server (no developer mode, sign-up off, password policy) | Done | FINAL_QA_REPORT § A3 |
| 9 | QA pharmacy: every workflow A3–A22 PASS over HTTP as real users (309 / 309) | Done | `qa/local/results.json`, FINAL_QA_REPORT |
| 10 | Permission matrix by real sign-in, every cell as designed | Done | `qa/local/permissions-matrix.md` |
| 11 | Security pass: P0 = P1 = P2 = 0 (exploits replayed and refused) | Done | FINAL_QA_REPORT § A21, § Issues |
| 12 | Backup → restore round trip; tampered / planted backups refused; failed restore reverted | Done | A15 rows |
| 13 | Real update 1.0.0-rc.2 → 1.0.0-rc.3 (backup first, data kept, translations recompiled); same build again = current; failed update rolled back to the exact previous build; one server operation at a time | Done | A16 rows |
| 14 | Startup, recovery and restart screens of the desktop app | Done in rc.2, code unchanged | Not re-run in rc.3 (they were driven by the withdrawn capture script); desktop code unchanged since rc.2 except its version; `npm test` (guards, local server), Windows CI first launch |
| 15 | Offline: no internet and an unreachable Cloud never stop the counter | Done | A18 rows |
| 16 | Scheduled jobs (hourly backups) recover when the computer's clock is put back | Done | `test_backup.TestSchedulerClock`; A3 "no scheduled job waits in the future" |
| 17 | Arabic terminology: a cash/POS shift is «شِفت» / «شِفتات» with masculine agreement, on the counter, in server messages, the report and the sidebar; the installer and the update compile and serve it | Done | `qa/check_terminology.py` (CI step), `tests/test_terminology.py`, A3 and A16 Arabic rows, A10/A11/A21 server messages |
| 18 | Docs current for rc.3 (install, release, localization, POS, product, status) | Done | `pharmacyos/docs/` |
| 19 | No secrets, test data or QA scripts in the shipped bundle | Done | `scripts/prepare-server-bundle.js` copies only `deploy/`, `dev/setup-dev-bench.sh` and the app without tests |

## Before a public release (owner)

| # | Item | Why it is not done here |
|---|---|---|
| 20 | Code-signing certificate (`WINDOWS_CSC_LINK`, `WINDOWS_CSC_KEY_PASSWORD` secrets) | External purchase; without it SmartScreen warns. The build says `signed: no` — nothing is faked. |
| 21 | Install on physical Windows 10 and Windows 11 PCs (WSL2 enablement, reboot-and-continue, boot task, port 80) | Needs real PCs; CI's Windows Server cannot run WSL2 nested |
| 22 | A real USB/Bluetooth barcode scanner (HID) on the counter | Software tested with simulated HID keystrokes (Enter and Tab suffix); no physical scanner here |
| 23 | 80 mm and 58 mm thermal printers, and a cash drawer kicked by the printer | Receipts verified as rendered pages only; no printer was attached |
| 24 | A pilot pharmacy running full days | Operational validation |
| 25 | Tag `pharmacyos-local-v1.0.0-rc.3` → draft release → a person presses Publish | Publishing is the owner's decision; this pass never tags or publishes |
| 26 | Merge the development branch | Not done by design (no merge to main in this task) |

## Known limits (documented, not defects)

* **Secondary counter PCs need the server PC.** In multi-PC mode the other counters open the server
  over the LAN; if the server PC is off or unreachable they show the recovery screen and cannot sell until
  it is back (no client-side offline queue in v1). Single-PC mode is fully offline.
* **Cloud sync is not part of v1.** The outbox queues website events and retries them; nothing sells or
  blocks on the Cloud.
* About 26 % of upstream ERPNext desk strings are still English; every PharmacyOS screen is Arabic-first.
* ERPNext's own Arabic for an asset-depreciation shift or a workstation shift (7 upstream strings) keeps its
  upstream word: it is not a cash shift (`FORK_PATCHES.md`, `qa/check_terminology.py`).
