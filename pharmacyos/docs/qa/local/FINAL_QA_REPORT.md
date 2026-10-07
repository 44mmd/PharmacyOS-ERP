# PharmacyOS Local ERP 1.0.0-rc.2 — final QA report

Final certification of the LOCAL ERP (Phase A). Every result below comes from the running product: a QA
pharmacy on a server **installed from nothing** and brought to the final build by the guided update, driven over HTTP and in a
browser as its real users, plus the desktop app and Windows CI. Nothing was mocked or edited. Where a test
could not be physical (Windows PC with WSL2, scanner, thermal printer, cash drawer) this report says so and
claims nothing.

## Environment

| | |
|---|---|
| Server | `install-server.sh` (what the Windows setup runs inside WSL) in a chroot of the **exact Ubuntu 24.04 WSL root filesystem** the setup imports (`ubuntu-wsl.tar.gz`, SHA-256 verified by the setup), on Linux x86-64, 4 CPUs. Test-only shims: this sandbox's proxy CA/env, `systemctl` → init scripts (a chroot has no systemd). |
| Software | PharmacyOS ERP **1.0.0rc2**: fresh install of server build `4d08257dc563`, then the guided update to the final build `3311dd6cfc7d` (one fix in the update lock), on which the A15 failed-restore and all A16 checks were run again; Frappe **16.36.1** (`97a5dd93`), ERPNext **16.37.0** (`af63cde4`); Python 3.14.6, Node 24.21.0, MariaDB 10.11, Redis (ACL password), nginx, supervisor. |
| Site | `pharmacy.local`, production mode, Arabic, Asia/Baghdad, IQD. |
| Clients | HTTP sessions per user (`qa/local/qalib.py`); Chromium 1194 via Playwright (browser screens); the PharmacyOS ERP desktop app (Electron 44.5.1) run from source on Linux + Xvfb with `wsl.exe` replaced by a script that enters the QA server; Windows CI (`windows-latest`, Windows Server 2025, Windows PowerShell 5.1) for the installer and the setup screens. |
| Final QA run | 2026-10-07 (UTC): install 17:06–17:13, QA 17:13–17:22, screens 17:30–17:40, update to the final build and A16 re-run 17:45–17:55. Scripts: `qa/local/qa_*.py`, `capture_screens.mjs`, `capture_desktop.mjs`. Raw results: `qa/local/results.json`. |

### Real vs simulated (read this first)

| Area | What was real | What was simulated / not tested |
|---|---|---|
| Server install | The real `install-server.sh` from an empty Ubuntu 24.04 WSL image; resumable steps; production mode | WSL2 itself on a physical Windows PC (enablement, reboot-and-continue, boot task) |
| Windows setup screens | Real Windows (CI): welcome, details, the **real system check**, installer built/installed/uninstalled | The installing / restart / done screens on CI are driven by a state file (CI cannot run WSL2); Install is never pressed there |
| Desktop app | Real Electron app: startup, update, backups, restore, recovery, About — against the real QA server | Run on Linux; `wsl.exe` replaced by a script entering the QA server (a developer hook the installed app ignores) |
| Barcode scanning | HID behaviour reproduced exactly: keystrokes typed into the page with Enter / Tab suffixes, fast consecutive scans, several barcodes per medicine, batch barcodes | **No physical scanner was used. No scanner certification is claimed.** |
| Receipts | The real `PharmacyOS Receipt` print view rendered at 80 mm and 58 mm (Arabic/English, discount, change, return) | **No thermal printer was attached; no physical printing, paper cutting or cash-drawer kick is claimed.** |
| Offline | The server's internet really cut (firewall rules on its processes, direct and via proxy); Cloud address really unreachable | — |
| Multi-PC | Server answering on the LAN address | A second physical counter PC was not available |

## Results (final QA run)

**301 / 301 checks PASS** (0 FAIL).

| Workflow | Checks | PASS | FAIL | Screenshots | Automated tests |
|---|---|---|---|---|---|
| A3 Fresh install | 14 | 14 | 0 | [windows/01-setup-welcome](screenshots/windows/01-setup-welcome.png), [windows/02-setup-pharmacy-details](screenshots/windows/02-setup-pharmacy-details.png), [windows/03-setup-system-check-windows](screenshots/windows/03-setup-system-check-windows.png), [windows/04-setup-installing](screenshots/windows/04-setup-installing.png), [windows/06-setup-done](screenshots/windows/06-setup-done.png), [05-login](screenshots/05-login.png), [06-dashboard](screenshots/06-dashboard.png) | install-command.test.js, setup.test.js, Windows CI |
| A5 QA pharmacy | 4 | 4 | 0 | [06-dashboard](screenshots/06-dashboard.png), [21-medicines](screenshots/21-medicines.png), [21b-medicine-form](screenshots/21b-medicine-form.png), [26-suppliers](screenshots/26-suppliers.png), [31-employees](screenshots/31-employees.png) | test_v1_certification (owner keeps the staff directory), test_local_journeys |
| A6 Purchasing | 11 | 11 | 0 | [27-purchase-order](screenshots/27-purchase-order.png), [28-receiving](screenshots/28-receiving.png), [29-purchase-return](screenshots/29-purchase-return.png) | test_operations, test_local_journeys, test_v1_operations (supplier return), test_v1_certification (buyer receives with batch) |
| A7 Inventory | 8 | 8 | 0 | [22-inventory](screenshots/22-inventory.png), [23-batches](screenshots/23-batches.png), [24-expiry](screenshots/24-expiry.png) | test_pharmacy_core, test_v1_operations (no re-dating) |
| A8 POS shift | 25 | 25 | 0 | [19-shift-open](screenshots/19-shift-open.png), [07-pos](screenshots/07-pos.png), [08-populated-cart](screenshots/08-populated-cart.png), [08b-pos-arabic-search](screenshots/08b-pos-arabic-search.png), [09-payment](screenshots/09-payment.png), [10-receipt-80mm-pos](screenshots/10-receipt-80mm-pos.png), [12-hold](screenshots/12-hold.png), [13-resume-list](screenshots/13-resume-list.png), [13b-resumed-cart](screenshots/13b-resumed-cart.png), [14-sales-history](screenshots/14-sales-history.png) | test_web_pos, test_pos_shift, test_v1_certification (counter pricing) |
| A9 Returns | 6 | 6 | 0 | [16-return](screenshots/16-return.png), [16b-return-receipt](screenshots/16b-return-receipt.png) | test_return_integrity_round4, test_web_pos |
| A10 Void | 19 | 19 | 0 | [17-void-approval](screenshots/17-void-approval.png), [18-void-result](screenshots/18-void-result.png), [15-sale-detail](screenshots/15-sale-detail.png) | test_v1_operations (void, approver lock-out) |
| A11 Shift close | 5 | 5 | 0 | [20-shift-close](screenshots/20-shift-close.png), [20b-shift-closed](screenshots/20b-shift-closed.png) | test_pos_close_shift |
| A12 Expired disposal | 12 | 12 | 0 | [25-expired-disposal](screenshots/25-expired-disposal.png), [24-expiry](screenshots/24-expiry.png) | test_v1_operations (disposal) |
| A13 Reports | 10 | 10 | 0 | [30-reports](screenshots/30-reports.png), [40-permission-denied](screenshots/40-permission-denied.png) | test_v1_operations (report), test_cost_exposure |
| A14 Permissions | 121 | 121 | 0 | [32-roles-permissions](screenshots/32-roles-permissions.png), [40-permission-denied](screenshots/40-permission-denied.png) | test_permissions_matrix, test_cost_exposure, test_credit_note_authority |
| A15 Backup / restore | 13 | 13 | 0 | [34-backups](screenshots/34-backups.png), [34b-backup-now-done](screenshots/34b-backup-now-done.png), [35-restore-confirm](screenshots/35-restore-confirm.png), [35b-restore-running](screenshots/35b-restore-running.png), [35c-restore-result](screenshots/35c-restore-result.png) | test_backup |
| A16 Update / rollback | 6 | 6 | 0 | [36-update-offered](screenshots/36-update-offered.png), [36b-update-running](screenshots/36b-update-running.png), [36c-update-result](screenshots/36c-update-result.png), [39-about-version](screenshots/39-about-version.png) | desktop: localserver.test.js (versions, builds) |
| A18 Offline | 13 | 13 | 0 | — | test_integration, test_sync (outbox retry and back-off) |
| A19 Barcode (simulated HID) | 5 | 5 | 0 | [08-populated-cart](screenshots/08-populated-cart.png), [08b-pos-arabic-search](screenshots/08b-pos-arabic-search.png) | test_web_pos (scan) |
| A20 Receipts (rendered) | 5 | 5 | 0 | [10-receipt-80mm](screenshots/10-receipt-80mm.png), [11-receipt-58mm](screenshots/11-receipt-58mm.png), [10-receipt-80mm-pos](screenshots/10-receipt-80mm-pos.png), [16b-return-receipt](screenshots/16b-return-receipt.png) | test_experience (receipt), test_local_journeys |
| A21 Security | 15 | 15 | 0 | [40-permission-denied](screenshots/40-permission-denied.png) | test_v1_certification, test_remediation, test_cost_exposure |
| A22 Stress | 9 | 9 | 0 | — | test_concurrency_round4, test_return_concurrency |


## Issues found and fixed in this pass (bug policy A25)

P0 = data loss / security / unusable; P1 = major workflow broken; P2 = important with a workaround; P3 = polish.
Every P0–P2 below was fixed **and re-tested** (the re-test is the row or test named).

| Sev | Issue (how it was found) | Fix | Re-test |
|---|---|---|---|
| P0 | A cashier could sell at 1 IQD, give 100 % off or make a credit invoice through the document API (QA exploit) | `counter_sales.py`: counter sales only through the POS, own shift, today, counter price list, ERPNext's prices, discount ceiling — on the invoice itself | A21 rows; `test_v1_certification` |
| P0 | Counter staff could rewrite a sale's return ledger and return the same goods twice (audit) | Ledger at a permission level no role holds | A21 "cannot rewrite … ledger", "returned twice"; `test_v1_certification` |
| P0 | Restore went ahead when its safety backup had failed; a failed restore left the site live on a half-imported database (audit) | `restore-backup.sh`: safety backup checked first, maintenance mode kept on failure, exit statuses 0/3/4/5 | A15 rows (tampered, planted, failed import reverted) |
| P1 | Expired batches could be sold by back-dating the sale (audit) | Counter sales dated today; expired batches cannot be re-dated | A21 "back-dates a sale"; `test_v1_operations` |
| P1 | Windows setup: values passed through two shell parses — pharmacy names truncated, passwords changed (audit) | Values in a file in the locked Setup folder; `--exec bash` | Windows CI PowerShell 5.1 step; `install-command.test.js` |
| P1 | Owner could not add Employees (403) (QA) | Staff-master rights for the owner | A5 rows; `test_v1_certification` |
| P1 | Purchasing Officer could not receive (no Batch create) (QA) | Batch create/read for the buyer | A6 rows; `test_v1_certification` |
| P1 | An update failed on a database query (`Singles` ordered by a missing column) — correctly rolled back (QA) | Raw read of the stored setting | A16 rows |
| P1 | Desktop app: privileged IPC trusted the window URL, not the sending frame (audit) | Sender-frame guard | `guards.test.js` |
| P2 | Bench Redis had no password; reachable from every Windows account through WSL localhost (audit) | ACL password (bench uses an ACL file, where `requirepass` is ignored) | A21 "Redis refuses unauthenticated clients" |
| P2 | Cached Ubuntu image and backups folder writable/plantable by any Windows user (audit) | SHA-256 check of the image; data folder locked to SYSTEM/Administrators/installing user; links removed | Windows CI lockdown step; A15 "planted folder refused" |
| P2 | Discount ceiling read as 0 on an updated pharmacy (QA) | Stored default on migrate | `test_v1_certification` |
| P2 | Rollback wrote the release folder name into VERSION (QA) | Exact previous version/build restored | A16 rows |
| P2 | Purchasing Officer could write stock off (QA) | Disposal needs an owner/manager/inventory role; buyer has no Stock User | A14 matrix "Dispose" |
| P2 | Automatic promotions (Pricing Rules) above the ceiling blocked every cashier sale (audit) | Ceiling counts only the counter's own discount | `test_v1_certification.test_a_promotion_is_not_a_counter_discount` |
| P1 | **No scheduled job ran for the first 2½ hours after every fresh install** — no hourly backup, no Cloud outbox: the jobs are created while a new site is still in Asia/Kolkata (+5:30) and first run switches to Asia/Baghdad (+3), leaving their times in the future; the same after a clock correction (QA: offline test on a fresh server, no delivery attempt) | `repair_job_clock` right after the time zone is set, on any time-zone change, at every start and after every migrate | `TestSchedulerClock` (2 tests); A3 "no scheduled job waits in the future"; A18 outbox rows |
| P2 | Desktop app said "nothing changed" when a rollback or a restore's own recovery had failed (review) | Exact outcome shown | screenshots 35c / 36c; `npm test` |
| P2 | A fresh install aborted when `supervisorctl restart all` returned 7 for a program still starting; the same in an update would roll back a good update (QA server) | Wait for supervisord; the web health check decides | Final fresh install (A3 rows); A16 rows |
| P2 | Counter staff could change a customer's group or price list (a group promotion would then apply at the counter) (audit) | Customer terms are a manager's decision; counter staff edit contact details | `test_v1_certification.test_counter_staff_do_not_change_customer_terms` |
| P2 | A second update asked for while one was running answered "already up to date" (it checked the release link before taking the lock), although the first could still roll back (QA lock test) | Lock taken first | A16 "one server operation at a time" |
| P2 | Packaged app: Reload during an update/restore could start a second one (audit) | Reload removed; one server operation at a time (`flock`, busy guard) | A16 "busy" row |

**Remaining: P0 = 0, P1 = 0, P2 = 0.** Remaining P3 (documented, not blocking): upstream ERPNext desk
strings ~26 % English; the LAN share is plain HTTP inside the pharmacy network (firewall limited to the local
subnet); the database root password reaches `bench` as a command-line argument inside the WSL environment
(root-only); held sales live in the counter browser's storage until the end of the day.

## Automated tests

| Suite | Result | Where |
|---|---|---|
| PharmacyOS app (`bench run-tests --app pharmacyos_erp`) | **260 tests, OK** (273 s) on Frappe 16.36.1 / ERPNext 16.37.0 | development bench, final code |
| Desktop app (`npm test`) | **29 / 29** | Linux and Windows CI |
| Windows CI (`.github/workflows/pharmacyos-desktop.yml`) | green: PowerShell 5.1 parse + helpers + data-folder lock (planted file and planted link), system check on real Windows, build, silent install, first launch, setup screens, uninstall keeps data | GitHub Actions `windows-latest` |
| QA over HTTP (`qa/local/qa_*.py`) | **301 / 301 checks PASS** (tables above) | QA server: fresh install, then the guided update to the final build |

## A4 — Windows CI on the final commit

Every push that touches the app, the desktop or the deploy scripts builds `PharmacyOS-Setup-1.0.0-rc.2.exe` on
`windows-latest`, installs it silently, opens it, captures the setup screens and uninstalls it (data kept).
The run that built the final commit, its installer's size and SHA-256, and whether it is signed are in
[`screenshots/windows/INSTALLER.txt`](screenshots/windows/INSTALLER.txt), written by CI itself
(`workflow_dispatch` with `publish_evidence`). The installer is **unsigned** (no certificate is configured;
nothing is faked).

## A17 — server failure and recovery

* **Startup** ([37](screenshots/37-startup.png)): the app shows a branded startup screen while it starts the
  server and waits for it.
* **Recovery** ([38](screenshots/38-recovery.png)): the server never answered (keep-alive replaced by one that
  does nothing): the app shows what to do in order — Retry, Start the server, Settings — and that no saved sale
  is lost. **Start the server** ([38b](screenshots/38b-recovery-starting.png)) brings it back and the app
  continues ([38c](screenshots/38c-recovered.png)).
* A redirect away from the server shows the recovery screen, never a blank page (`guards.test.js`).

## A18 — offline (secondary PCs)

Single-PC mode is fully offline: the A18 rows ran a whole shift with the server's internet cut and the Cloud
unreachable. **Multi-PC limit (documented):** other counter PCs open the server over the LAN; when the server PC
is off or unreachable they show the recovery screen and cannot sell until it returns (v1 has no client-side
offline queue). This is a design limit, not a defect, and is in `RELEASE_CHECKLIST.md`.

## A23 — screenshot index (40 required screens)

All captured from the running product in this pass (`docs/qa/local/screenshots/`). Browser screens: Chromium
against the final QA server. Desktop screens: the Electron app against the QA server. Windows screens: Windows CI.

| # | Screen | File(s) |
|---|---|---|
| 1 | Setup | [windows/01-setup-welcome](screenshots/windows/01-setup-welcome.png), [windows/02-setup-pharmacy-details](screenshots/windows/02-setup-pharmacy-details.png) |
| 2 | System Check (real Windows) | [windows/03-setup-system-check-windows](screenshots/windows/03-setup-system-check-windows.png) |
| 3 | Installation Progress | [windows/04-setup-installing](screenshots/windows/04-setup-installing.png), [windows/05-setup-restart-needed](screenshots/windows/05-setup-restart-needed.png) (state-driven on CI) |
| 4 | First Launch | [windows/06-setup-done](screenshots/windows/06-setup-done.png), [windows/00-first-run-smoke](screenshots/windows/00-first-run-smoke.png), [04b-first-launch-after-update](screenshots/04b-first-launch-after-update.png) |
| 5 | Login | [05-login](screenshots/05-login.png) |
| 6 | Dashboard | [06-dashboard](screenshots/06-dashboard.png) |
| 7 | POS | [07-pos](screenshots/07-pos.png) |
| 8 | Populated Cart | [08-populated-cart](screenshots/08-populated-cart.png), [08b-pos-arabic-search](screenshots/08b-pos-arabic-search.png) |
| 9 | Payment | [09-payment](screenshots/09-payment.png) |
| 10 | Receipt 80 mm | [10-receipt-80mm](screenshots/10-receipt-80mm.png), [10-receipt-80mm-pos](screenshots/10-receipt-80mm-pos.png) |
| 11 | Receipt 58 mm | [11-receipt-58mm](screenshots/11-receipt-58mm.png) |
| 12 | Hold | [12-hold](screenshots/12-hold.png) |
| 13 | Resume | [13-resume-list](screenshots/13-resume-list.png), [13b-resumed-cart](screenshots/13b-resumed-cart.png) |
| 14 | Sales History | [14-sales-history](screenshots/14-sales-history.png) |
| 15 | Sale Detail | [15-sale-detail](screenshots/15-sale-detail.png) |
| 16 | Return | [16-return](screenshots/16-return.png), [16b-return-receipt](screenshots/16b-return-receipt.png) |
| 17 | Void Approval | [17-void-approval](screenshots/17-void-approval.png) |
| 18 | Void Result | [18-void-result](screenshots/18-void-result.png) |
| 19 | Shift Open | [19-shift-open](screenshots/19-shift-open.png) |
| 20 | Shift Close | [20-shift-close](screenshots/20-shift-close.png), [20b-shift-closed](screenshots/20b-shift-closed.png) |
| 21 | Medicines | [21-medicines](screenshots/21-medicines.png), [21b-medicine-form](screenshots/21b-medicine-form.png) |
| 22 | Inventory | [22-inventory](screenshots/22-inventory.png) |
| 23 | Batches | [23-batches](screenshots/23-batches.png) |
| 24 | Expiry | [24-expiry](screenshots/24-expiry.png), [24b-expired-batches](screenshots/24b-expired-batches.png) |
| 25 | Expired Disposal | [25-expired-disposal](screenshots/25-expired-disposal.png) |
| 26 | Suppliers | [26-suppliers](screenshots/26-suppliers.png) |
| 27 | Purchase Order | [27-purchase-order](screenshots/27-purchase-order.png) |
| 28 | Receiving | [28-receiving](screenshots/28-receiving.png) |
| 29 | Purchase Return | [29-purchase-return](screenshots/29-purchase-return.png) |
| 30 | Reports | [30-reports](screenshots/30-reports.png) |
| 31 | Employees | [31-employees](screenshots/31-employees.png) |
| 32 | Roles / Permissions | [32-roles-permissions](screenshots/32-roles-permissions.png), matrix: `qa/local/permissions-matrix.md` |
| 33 | Settings | [33-settings](screenshots/33-settings.png), [33b-system-status](screenshots/33b-system-status.png) |
| 34 | Backups | [34-backups](screenshots/34-backups.png), [34b-backup-now-done](screenshots/34b-backup-now-done.png) |
| 35 | Restore | [35-restore-confirm](screenshots/35-restore-confirm.png), [35b-restore-running](screenshots/35b-restore-running.png), [35c-restore-result](screenshots/35c-restore-result.png) |
| 36 | Update | [36-update-offered](screenshots/36-update-offered.png), [36b-update-running](screenshots/36b-update-running.png), [36c-update-result](screenshots/36c-update-result.png) |
| 37 | Startup | [37-startup](screenshots/37-startup.png) |
| 38 | Recovery | [38-recovery](screenshots/38-recovery.png), [38b-recovery-starting](screenshots/38b-recovery-starting.png), [38c-recovered](screenshots/38c-recovered.png) |
| 39 | About / Version | [39-about-version](screenshots/39-about-version.png) (native dialog captured from the X display) |
| 40 | Permission Denied | [40-permission-denied](screenshots/40-permission-denied.png) |

The desktop screens (4b, 34–39) were taken on the QA server earlier in this pass, at server build `a507ccb8`
(the update screen shows `d1d6481c → a507ccb8`); the desktop app's code is the same as in the final commit.
`screens.json` and `desktop-screens.json` record what each screen showed and any page errors (none).

## Permission matrix (A14, by real sign-in)

✓ = the server did it, ✗ = the server refused it; every cell matches the documented role model.

| Operation | Owner | Manager | Pharmacist | Cashier | Purchasing | Inventory | Accountant |
|---|---|---|---|---|---|---|---|
| Read medicines (Item list) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Counter sale (open shift + checkout) | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ |
| Void / cancel a sale directly | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Add a medicine with a price | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ |
| See purchase cost (buying Item Price) | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ | ✓ |
| Create a supplier | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ |
| Create a purchase order | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ |
| Receive stock (Purchase Receipt, batch + expiry) | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ | ✗ |
| Stock adjustment (Stock Reconciliation) | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ |
| Dispose of stock (write-off) | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ |
| Batches & Expiry page | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Pharmacy Report | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Read the void audit log | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Read the general ledger | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ |
| Add a staff account (User) | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Change PharmacyOS Settings | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Back up now | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |

## A26 — certification

| Area | Score | Basis |
|---|---|---|
| Core ERP workflows | **97 / 100** | Every A5–A13 workflow passed on a fresh install as the real users (180+ checks), in the browser and over HTTP. Not 100: multi-PC selling was exercised over the server's address only, not from a second physical PC. |
| Data integrity | **97 / 100** | Stock equals the stock ledger for every product after 60+ sales; idempotent checkout; return bounds; void reversal; restore verified and reverted on failure; scheduled backups re-armed. Not 100: no power-cut test on a physical PC. |
| Security | **94 / 100** | P0 = P1 = P2 = 0; every exploit replayed and refused; 121-cell permission matrix as designed; Redis password; CSP on `/pos`. Remaining P3: plain HTTP on the pharmacy LAN, root password as a CLI argument inside WSL, unsigned installer. |
| Installer / update / recovery | **88 / 100** | Real install script from an empty Ubuntu 24.04 WSL image (twice, plus a forced-failure resume); real Windows install/uninstall on CI; update and rollback; restore; recovery. Not higher: WSL2 enablement, reboot-and-continue and the boot task need a physical Windows 10/11 PC. |
| UI / UX | **91 / 100** | Arabic-first, RTL, IQD on every PharmacyOS screen (40 screens); keyboard-first counter. ~26 % of upstream ERPNext desk strings still English. |
| Automated QA | **97 / 100** | 260 unit/integration tests, 29 desktop tests, 301 HTTP checks, Windows CI. |
| Simulated hardware readiness | **90 / 100** | HID scanning (Enter/Tab, rapid, several barcodes, batch barcodes) and receipts at 80/58 mm verified as software. |
| Physical hardware validation | **0 / 100** | No physical Windows PC, scanner, thermal printer or cash drawer was available. Nothing is claimed. |

**LOCAL SOFTWARE READINESS: 95 %**

**LOCAL PHYSICAL VALIDATION: 0 %** (needs the owner's devices: a Windows 10/11 PC, a scanner, 80/58 mm
printers, a cash drawer — see `RELEASE_CHECKLIST.md` items 21–24).

## Every check (expected / actual / result)

### A3 Fresh install

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | production mode (no developer mode, no test runner) | developer_mode and allow_tests off | {"developer_mode": 0, "allow_tests": null} | **PASS** | 17:13 |
| 2 | no sample data | 0 items, customers (besides walk-in), invoices, suppliers | {"items": 0, "customers": 1, "invoices": 0, "suppliers": 0} | **PASS** | 17:13 |
| 3 | one owner account, nobody else | owner@… only | ["owner@qa-pharmacy.test"] | **PASS** | 17:13 |
| 4 | the owner holds the Pharmacy Owner profile | Pharmacy Owner + System Manager | ["Accounts Manager", "Accounts User", "All", "Desk User", "Guest", "Inventory Manager", "Item Manager", "Pharmacy Accountant", "Pharmacy Manager", "Pharmacy Owner", "Purchase Manager", "Purchase User", "Purchasing Officer", "Report Manager", "Sales Manager", " | **PASS** | 17:13 |
| 5 | company, branch and warehouse created by the setup | one company, one branch with its warehouse | {"companies": ["QA TEST Pharmacy"], "branches": [{"name": "Main Branch", "pharmacyos_warehouse": "Main Branch - QTP"}]} | **PASS** | 17:13 |
| 6 | first counter ready (Main Counter, cash, receipt format) | POS Profile with warehouse, price list, PharmacyOS Receipt | [{"name": "Main Counter", "warehouse": "Main Branch - QTP", "print_format": "PharmacyOS Receipt", "selling_price_list": "Standard Selling"}] | **PASS** | 17:13 |
| 7 | password policy on, sign-up off | policy enabled, sign-up disabled | {"enable_password_policy": 1, "minimum_password_score": "2", "disable_signup": 1} | **PASS** | 17:13 |
| 8 | scheduler on (hourly backups) | enabled | true | **PASS** | 17:13 |
| 9 | no scheduled job waits in the future (time zone set at first run) | none | none | **PASS** | 17:13 |
| 10 | Arabic, Iraq time zone, IQD | ar / Asia/Baghdad / IQD | {"language": "ar", "time_zone": "Asia/Baghdad", "currency": "IQD"} | **PASS** | 17:13 |
| 11 | counter discount ceiling stored (10 %) | 10 | [["10"]] | **PASS** | 17:13 |
| 12 | the owner signs in and lands on the Pharmacy Dashboard | HTTP 200 | HTTP 200 | **PASS** | 17:13 |
| 13 | self sign-up refused | refused (sign-up disabled) | HTTP 417 | **PASS** | 17:13 |
| 14 | all server programs running | web, socket.io, scheduler, 2 workers, 2 Redis | ["frappe-bench-redis:frappe-bench-redis-cache", "frappe-bench-redis:frappe-bench-redis-queue", "frappe-bench-web:frappe-bench-frappe-web", "frappe-bench-web:frappe-bench-node-socketio", "frappe-bench-workers:frappe-bench-frappe-long-worker-0", "frappe-bench-wo | **PASS** | 17:13 |

### A5 QA pharmacy

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | owner signs in | the owner created by setup can sign in | owner@qa-pharmacy.test | **PASS** | 17:13 |
| 2 | owner creates employee records linked to staff accounts | 7 employees linked to the staff users | [["QA Manager Omar", "manager@qa-pharmacy.test"], ["QA Pharmacist Huda", "pharmacist@qa-pharmacy.test"], ["QA Cashier Sara", "cashier@qa-pharmacy.test"], ["QA Cashier Ali", "cashier2@qa-pharmacy.test"], ["QA Purchasing Karim", "purchasing@qa-pharmacy.test"], [ | **PASS** | 17:13 |
| 3 | 20 medicines in 7 categories, batch + expiry tracked | 20 items, all has_batch_no and has_expiry_date | 20 items, groups=['Analgesics', 'Antibiotics', 'Chronic Care', 'Gastrointestinal', 'Personal Care', 'Respiratory & Allergy', 'Vitamins & Supplements'] | **PASS** | 17:13 |
| 4 | multiple barcodes on one medicine | Panadol 500 has 2 barcodes | ["6291100000011", "5000158062474"] | **PASS** | 17:13 |

### A6 Purchasing

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | supplier creation by the Purchasing Officer | 2 suppliers created by purchasing@ | [{"name": "QA Baghdad Pharma Supply", "owner": "purchasing@qa-pharmacy.test"}, {"name": "QA Basra Medical Trading", "owner": "purchasing@qa-pharmacy.test"}] | **PASS** | 17:13 |
| 2 | purchase order created and submitted by the Purchasing Officer | PO with 8 lines, submitted, owner purchasing@ | ["PUR-ORD-2026-00001", "purchasing@qa-pharmacy.test"] | **PASS** | 17:13 |
| 3 | partial receiving against the PO | PO partly received (0 < per_received < 100) | {"receipt": "MAT-PRE-2026-00001", "per_received": 41.891892, "status": "To Receive and Bill"} | **PASS** | 17:13 |
| 4 | full receiving completes the PO | per_received = 100, status To Bill | {"receipt": "MAT-PRE-2026-00002", "per_received": 100.0, "status": "To Bill"} | **PASS** | 17:13 |
| 5 | second supplier PO received in full | PO2 100% received | MAT-PRE-2026-00003 | **PASS** | 17:13 |
| 6 | back-dated delivery (now expired / near-expiry batches) + fresh batch | receipts submitted | ["MAT-PRE-2026-00004", "MAT-PRE-2026-00005"] | **PASS** | 17:13 |
| 7 | receiving an expired batch today is refused | server refuses an expired batch on today's receipt | refused HTTP 417: الصف رقم 1: انتهت صلاحية الوجبة AMX-QA-EXPIRED بالفعل. | **PASS** | 17:13 |
| 8 | purchase return to supplier takes stock out of the received batch | BRU-QA-2601 goes 50 → 48 | {"return": "MAT-PRE-2026-00006", "before": 50.0, "after": 48.0} | **PASS** | 17:13 |
| 9 | received cost becomes the stock valuation | incoming rate 1500 for Panadol | [{"incoming_rate": 1500.0, "actual_qty": 50.0, "valuation_rate": 1500.0}] | **PASS** | 17:13 |
| 10 | two batches of one medicine with different expiry | Panadol 50 + 50 in two batches | {"PAN-QA-2601": 50.0, "PAN-QA-2602": 50.0} | **PASS** | 17:13 |
| 11 | inventory after purchasing (Bin = received − returned) | Brufen 48, Panadol 100, Insulin none | {"QA-BRUF400": 48.0, "QA-AZI500": 20.0, "QA-ZYRTEC": 30.0, "QA-PANEXT": 40.0, "QA-PAN500": 100.0, "QA-OMEP20": 40.0, "QA-AUG1G": 30.0, "QA-GLUCO500": 60.0, "QA-LIPITOR20": 20.0, "QA-VENTOLIN": 12.0, "QA-VITC1000": 30.0, "QA-CIPRO500": 3.0, "QA-GAVISCON": 15.0, | **PASS** | 17:13 |

### A7 Inventory

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | stock per batch (Batches & Expiry) | every received batch listed with qty and expiry | 22 batches; counts={'expired': 1, '30': 1, '60': 1, '90': 1, 'all': 22} | **PASS** | 17:15 |
| 2 | expired batch identified | AMX-QA-2509 flagged Expired | [["AMX-QA-2509", 8.0, "2026-10-01"]] | **PASS** | 17:15 |
| 3 | near-expiry batch identified | VOL-QA-2510 within 30 days | [["VOL-QA-2510", 25, "critical"]] | **PASS** | 17:15 |
| 4 | FEFO order | Augmentin: AUG-QA-2600 (earlier expiry) before AUG-QA-2601 | [["AUG-QA-2600", "2027-02-04"], ["AUG-QA-2601", "2027-08-03"]] | **PASS** | 17:15 |
| 5 | expired batch excluded from sellable stock | Amoxil sellable = AMX-QA-2604 only (12) | [["AMX-QA-2604", 12.0]] | **PASS** | 17:15 |
| 6 | low stock / out of stock (Inventory Health) | Cipro low (3 < 5), Insulin out of stock | {"rows": [{"item_code": "QA-INSULIN", "item_name": "Insulin Glargine Pen (cold chain)", "name_ar": "\u0642\u0644\u0645 \u0625\u0646\u0633\u0648\u0644\u064a\u0646 \u063a\u0644\u0627\u0631\u062c\u064a\u0646", "uom": "Nos", "is_medicine": 1, "qty": 0.0, "availabl | **PASS** | 17:15 |
| 7 | stock adjustment (count) by the Inventory Manager | Nivea 10 → 9, ledger entry written | {"reconciliation": "MAT-RECO-2026-00001", "before": 10.0, "after": 9.0} | **PASS** | 17:15 |
| 8 | cashier cannot adjust stock | Stock Reconciliation refused for a cashier | refused HTTP 403: لا يملك المستخدم cashier@qa-pharmacy.test حق الوصول إلى النمط عبر إذن دور للمستند جرد المخزون | **PASS** | 17:15 |

### A8 POS shift

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | open shift with 25,000 IQD | an open POS Opening Entry for the cashier | {"name": "POS-OPE-2026-00001", "company": "QA TEST Pharmacy", "pos_profile": "Main Counter", "period_start_date": "2026-10-07 20:13:49.801747"} | **PASS** | 17:15 |
| 2 | T1 scan + exact cash | 2 × 2,500 = 5,000 | ["ACC-SINV-2026-00004", 5000.0] | **PASS** | 17:15 |
| 3 | T2 item discount 10% + cash received 10,000 + change | 2,500 + 2,700 = 5,200; change 4,800 | ["ACC-SINV-2026-00005", 5200.0, 4800.0, 10000.0] | **PASS** | 17:15 |
| 4 | Arabic search | بنادول اكسترا finds Panadol Extra (أ/ا normalised) | ["QA-PANEXT"] | **PASS** | 17:15 |
| 5 | T3 transaction discount 5% | 7,000 − 5% = 6,650 | ["ACC-SINV-2026-00006", 6650.0, 350.0] | **PASS** | 17:15 |
| 6 | English search (partial) | omepra finds Omeprazole | ["QA-OMEP20"] | **PASS** | 17:15 |
| 7 | T4 quantity 3 | 3 × 3,500 = 10,500 | ["ACC-SINV-2026-00007", 10500.0] | **PASS** | 17:15 |
| 8 | T5 FEFO batch picked automatically | AUG-QA-2600 | [["QA-AUG1G", 1.0, "AUG-QA-2600"]] | **PASS** | 17:15 |
| 9 | T6 expired batch skipped automatically | AMX-QA-2604, never AMX-QA-2509 | [["QA-AMOXSYR", 1.0, "AMX-QA-2604"]] | **PASS** | 17:15 |
| 10 | T7 five-line sale, change from 40,000 | 5,500+8,000+4,500+6,500+6,000 = 30,500; change 9,500 | ["ACC-SINV-2026-00010", 30500.0, 9500.0] | **PASS** | 17:15 |
| 11 | T8 sale later partly returned | 4 × 4,000 + 2 × 5,500 = 27,000 | ["ACC-SINV-2026-00011", 27000.0] | **PASS** | 17:15 |
| 12 | T9 sale later voided | 2 × 4,000 = 8,000 | ["ACC-SINV-2026-00012", 8000.0] | **PASS** | 17:15 |
| 13 | T10 duplicate submission → one sale | the retry returns the same invoice; 1 invoice for the request | {"first": "ACC-SINV-2026-00013", "retry": "ACC-SINV-2026-00013", "replayed": true, "invoices": 1} | **PASS** | 17:15 |
| 14 | T11 cash 20,000 with change | 12,000; change 8,000 | ["ACC-SINV-2026-00014", 12000.0, 8000.0] | **PASS** | 17:15 |
| 15 | T12 scanned near-expiry batch sold | VOL-QA-2510 (not expired) sells | ACC-SINV-2026-00015 | **PASS** | 17:15 |
| 16 | overselling refused | Cipro qty 10 > 3 in stock | refused HTTP 417: For the item QA-CIPRO500, the Available qty 3.0 is less than the Required Qty 10.0 in the warehouse Main Branch - QTP. Please add sufficient qty in the warehouse. | **PASS** | 17:15 |
| 17 | out-of-stock item cannot be sold | Insulin (never received) | refused HTTP 417: Serial No / Batch No are mandatory for Item QA-INSULIN | **PASS** | 17:15 |
| 18 | selling the expired batch refused | AMX-QA-2509 refused at checkout | refused HTTP 417: الصف رقم 1: انتهت صلاحية الوجبة AMX-QA-2509 بالفعل. | **PASS** | 17:15 |
| 19 | price change refused on Main Counter | rate override refused (counter switch) | refused HTTP 403: تغيير السعر غير مسموح على هذا الكاونتر. | **PASS** | 17:15 |
| 20 | discount over 100% refused | discount 150% refused | refused HTTP 417: يجب أن يكون الخصم بين 0 و100%. | **PASS** | 17:15 |
| 21 | foreign payment method refused | a mode not on the counter is refused | refused HTTP 417: طريقة الدفع Wire Transfer غير متاحة على هذا الكاونتر. | **PASS** | 17:15 |
| 22 | sales history (own sales, newest first) | ≥ 12 sales listed | 15 | **PASS** | 17:15 |
| 23 | sales history search by invoice number | finds T8 | ["ACC-SINV-2026-00011"] | **PASS** | 17:15 |
| 24 | another cashier does not see these sales | cashier2 history excludes cashier's sales | 0 | **PASS** | 17:15 |
| 25 | receipt (reprint) of a sale | the PharmacyOS Receipt print view renders the sale | receipt with Panadol 500mg, batch and expiry | **PASS** | 17:15 |

### A9 Returns

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | partial return: 1 of 4 Glucophage, refund 4,000 | refund −4,000; return invoice submitted | ["ACC-SINV-2026-00016", -4000.0, -4000.0] | **PASS** | 17:15 |
| 2 | stock restored to the SAME batch | GLU-QA-2601 +1 | {"before": 56.0, "after": 57.0} | **PASS** | 17:15 |
| 3 | returnable quantity decreases | Glucophage 3 left, Zyrtec 2 | [["QA-GLUCO500", 3.0], ["QA-ZYRTEC", 2.0]] | **PASS** | 17:15 |
| 4 | cannot return more than sold | 4 more Glucophage refused (3 left) | refused HTTP 417: الصف # 1: لا يمكن الارجاع أكثر من 3.0 للبند QA-GLUCO500 | **PASS** | 17:15 |
| 5 | a return cannot be returned | return-of-return refused | refused HTTP 417: ACC-SINV-2026-00016 هي نفسها مرتجع. استخدم البيع الأصلي. | **PASS** | 17:15 |
| 6 | return audit: who / when / against | owner = cashier, return_against = T8 | ["cashier@qa-pharmacy.test", "2026-10-07 20:15:35.424172", "ACC-SINV-2026-00011"] | **PASS** | 17:15 |

### A10 Void

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | cashier cannot approve their own void | self-approval refused | refused HTTP 403: يجب أن يوافق شخص آخر على الإلغاء. | **PASS** | 17:15 |
| 2 | wrong manager password refused | refused, nothing changes | refused HTTP 403: رُفضت الموافقة: البريد أو كلمة المرور غير صحيحة. | **PASS** | 17:15 |
| 3 | another cashier cannot approve | cashier2 (no cancel right) refused as approver | refused HTTP 403: رُفضت الموافقة: البريد أو كلمة المرور غير صحيحة. | **PASS** | 17:15 |
| 4 | reason required | empty reason refused | refused HTTP 417: أدخل سبب الإلغاء. | **PASS** | 17:15 |
| 5 | cashier cannot void another cashier's sale | cashier2 asking to void cashier's sale refused | refused HTTP 403: يمكنك طلب إلغاء مبيعاتك فقط. | **PASS** | 17:15 |
| 6 | sale untouched after refusals | T9 still submitted (docstatus 1) | 1 | **PASS** | 17:15 |
| 7 | correct manager approval voids the sale | Void Log written, approved_by manager | {"name": "VOID-00002", "invoice": "ACC-SINV-2026-00012", "amount": 8000.0, "reason": "customer changed mind", "requested_by": "cashier@qa-pharmacy.test", "approved_by": "manager@qa-pharmacy.test", "voided_on": "2026-10-07 20:15:36.637698", "status": "voided"} | **PASS** | 17:15 |
| 8 | cashier stays signed in after the approval | get_context still answers as the cashier | cashier@qa-pharmacy.test | **PASS** | 17:15 |
| 9 | voided sale stays visible, marked voided | in history with voided = true | [{"name": "ACC-SINV-2026-00012", "customer_name": "زبون نقدي", "grand_total": 8000.0, "rounded_total": 8000.0, "currency": "IQD", "posting_date": "2026-10-07", "posting_time": "20:15:32.552563", "is_return": 0, "return_against": null, "status": "Cancelled", "d | **PASS** | 17:15 |
| 10 | never deleted: invoice exists as Cancelled | docstatus 2 | {"docstatus": 2, "status": "Cancelled"} | **PASS** | 17:15 |
| 11 | stock reversed into the batch | VTC-QA-2601 +2 | {"before": 27.0, "after": 29.0} | **PASS** | 17:15 |
| 12 | financial correction: ledger entries cancelled | all GL entries of T9 is_cancelled = 1 | [{"account": "Debtors - QTP", "debit": 0.0, "credit": 8000.0, "is_cancelled": 1}, {"account": "Sales - QTP", "debit": 0.0, "credit": 8000.0, "is_cancelled": 1}, {"account": "Cash - QTP", "debit": 8000.0, "credit": 0.0, "is_cancelled": 1}, {"account": "Debtors  | **PASS** | 17:15 |
| 13 | audit log readable by the manager | 1 Void Log row with amount 8,000 | [{"name": "VOID-00002", "amount": 8000.0, "requested_by": "cashier@qa-pharmacy.test", "approved_by": "manager@qa-pharmacy.test", "reason": "customer changed mind", "voided_on": "2026-10-07 20:15:36.637698", "pos_profile": "Main Counter"}] | **PASS** | 17:15 |
| 14 | cashier cannot read the void log | PharmacyOS Void Log list refused | refused HTTP 403: عدم كفاية الإذن PharmacyOS Void Log | **PASS** | 17:15 |
| 15 | voiding twice is idempotent | returns the same void record | VOID-00002 | **PASS** | 17:15 |
| 16 | a returned sale cannot be voided | T8 (partly returned) refused | refused HTTP 417: أُرجعت مواد من هذا البيع، فلم يعد بالإمكان إلغاؤه. | **PASS** | 17:15 |
| 17 | repeated wrong approvals lock the requester out | 6th attempt (correct password) refused: too many failed approvals | refused HTTP 403: محاولات موافقة فاشلة كثيرة. انتظر بضع دقائق ثم أعد المحاولة. | **PASS** | 17:15 |
| 18 | previous-day sale cannot be voided | refused: use a return | refused HTTP 417: يمكن إلغاء مبيعات اليوم فقط. استخدم الإرجاع للمبيعات الأقدم. | **PASS** | 17:15 |
| 19 | sale of a closed shift cannot be voided | refused: use a return | refused HTTP 417: هذا البيع ضمن وردية مغلقة (POS-CLO-2026-00001). استخدم الإرجاع بدلًا من ذلك. | **PASS** | 17:15 |

### A11 Shift close

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | expected cash = 25,000 + takings − refunds (voided excluded) | 25,000 + 127,350 (sum of the shift's submitted sales and returns) | {"mode_of_payment": "Cash", "label": "نقد", "opening_amount": 25000.0, "expected_amount": 152350.0, "closing_amount": 0.0, "difference": 0.0} | **PASS** | 17:15 |
| 2 | voided sale not in expected cash | T9's 8,000 excluded | 152350.0 | **PASS** | 17:15 |
| 3 | counted 500 short → variance −500 recorded | difference −500, closing entry submitted | {"name": "POS-CLO-2026-00001", "shift": "POS-OPE-2026-00001", "pos_profile": "Main Counter", "sales": 14, "grand_total": 127350.0, "net_total": 127350.0, "payments": [{"mode_of_payment": "Cash", "label": "نقد", "opening_amount": 25000.0, "expected_amount": 152 | **PASS** | 17:15 |
| 4 | a closed shift cannot be closed twice | second close refused | refused HTTP 417: ليست لديك وردية مفتوحة. | **PASS** | 17:15 |
| 5 | closing entry audit | owner = cashier, submitted, 11 sales | ["cashier@qa-pharmacy.test", 1, 14] | **PASS** | 17:15 |

### A12 Expired disposal

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | expired batch blocked at the POS | see A8: selling AMX-QA-2509 refused |  | **PASS** | 17:15 |
| 2 | cashier cannot dispose | disposal refused for a cashier | refused HTTP 403: غير مسموح لك بإتلاف المخزون. | **PASS** | 17:15 |
| 3 | cannot dispose more than the batch holds | 9 > 8 refused | refused HTTP 417: يوجد 8 فقط من الوجبة AMX-QA-2509 في Main Branch - QTP. | **PASS** | 17:15 |
| 4 | a healthy batch cannot be disposed as 'Expired' | AMX-QA-2604 refused | refused HTTP 417: الوجبة AMX-QA-2604 لم تنتهِ صلاحيتها بعد. اختر سببًا آخر إذا وجب إتلافها. | **PASS** | 17:15 |
| 5 | re-dating the expired batch refused (stock user) | expiry change refused | refused HTTP 403: انتهت صلاحية الوجبة AMX-QA-2509 في 01-10-2026. لا يمكن تغيير تاريخ وجبة منتهية الصلاحية: أتلفها بدلًا من ذلك. | **PASS** | 17:15 |
| 6 | re-dating the expired batch refused (manager) | expiry change refused | refused HTTP 403: انتهت صلاحية الوجبة AMX-QA-2509 في 01-10-2026. لا يمكن تغيير تاريخ وجبة منتهية الصلاحية: أتلفها بدلًا من ذلك. | **PASS** | 17:15 |
| 7 | re-dating the expired batch refused (owner) | an expired batch cannot be re-dated by anyone | refused HTTP 403: انتهت صلاحية الوجبة AMX-QA-2509 في 01-10-2026. لا يمكن تغيير تاريخ وجبة منتهية الصلاحية: أتلفها بدلًا من ذلك. | **PASS** | 17:15 |
| 8 | dispose the whole expired batch (8) | Stock Entry 'Expired Stock Disposal' submitted | {"name": "MAT-STE-2026-00001", "qty": 8.0, "batch_id": "AMX-QA-2509", "warehouse": "Main Branch - QTP", "reason": "Expired"} | **PASS** | 17:15 |
| 9 | stock deducted | AMX-QA-2509 → 0 | 0 | **PASS** | 17:15 |
| 10 | history: batch, qty, reason, user, time | row with reason Expired by stock@ | {"name": "MAT-STE-2026-00001", "posting_date": "2026-10-07", "posting_time": "20:15:40.881892", "owner": "stock@qa-pharmacy.test", "remarks": "إتلاف (منتهي الصلاحية) للوجبة AMX-QA-2509، انتهاء الصلاحية 2026-10-01. QA disposal", "reason": "Expired", "user": "QA | **PASS** | 17:15 |
| 11 | cancelling a disposal never makes the batch sellable | units back as AMX-QA-2509 (expired); sellable list still excludes it; cancel recorded | {"back_in_stock": 8.0, "sellable": ["AMX-QA-2604"], "docstatus": 2} | **PASS** | 17:15 |
| 12 | disposed of again | batch back to 0 | 0 | **PASS** | 17:15 |

### A13 Reports

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | period presets | today, yesterday, 7 days, month | {"today": ["2026-10-07", "2026-10-07"], "yesterday": ["2026-10-06", "2026-10-06"], "week": ["2026-10-01", "2026-10-07"], "month": ["2026-10-01", "2026-10-07"]} | **PASS** | 17:15 |
| 2 | today: transactions, sales, returns, net | counts match the shift | {'from_date': '2026-10-07', 'to_date': '2026-10-07', 'sales': {'gross': 139350.0, 'returns': 9500.0, 'net': 129850.0, 'transactions': 13, 'returns_count': 2, 'average': 10719.23}, 'discounts': {'total': 650.0, 'invoices': 2}, 'voids': {'count': 2, 'amount': 20 | **PASS** | 17:15 |
| 3 | discounts, voids, payment methods, top sellers, cashiers, shifts | sections present and filled | {"discounts": {"total": 650.0, "invoices": 2}, "voids": {"count": 2, "amount": 20200.0, "rows": [{"name": "VOID-00002", "invoice": "ACC-SINV-2026-00012", "amount": 8000.0, "reason": "customer changed mind", "requested_by": "cashier@qa-pharmacy.test", "approved | **PASS** | 17:15 |
| 4 | owner sees costs (purchases, stock value, profit) | costs_visible = 1 | true | **PASS** | 17:15 |
| 5 | manager: 7-day report | report returned | true | **PASS** | 17:15 |
| 6 | yesterday: the back-dated desk sale | 1 transaction yesterday | {"from_date": "2026-10-06", "to_date": "2026-10-06", "sales": {"gross": 0.0, "returns": 0.0, "net": 0.0, "transactions": 1, "returns_count": 0, "average": 0.0}, "discounts": {"total": 0.0, "invoices": 0}, "voids": {"count": 0, "amount": 0.0, "rows": []}, "paym | **PASS** | 17:15 |
| 7 | month to date | report returned | true | **PASS** | 17:15 |
| 8 | custom range over 366 days refused | refused | refused HTTP 417: اختر فترة لا تتجاوز سنة واحدة. | **PASS** | 17:15 |
| 9 | cashier cannot open the report | 403 | refused HTTP 403: تقرير الصيدلية لصاحب الصيدلية والمدراء والمحاسب. | **PASS** | 17:15 |
| 10 | pharmacist cannot open the report | 403 | refused HTTP 403: تقرير الصيدلية لصاحب الصيدلية والمدراء والمحاسب. | **PASS** | 17:15 |

### A14 Permissions

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | Read medicines (Item list) — Owner | allowed | allowed | **PASS** | 17:16 |
| 2 | Read medicines (Item list) — Manager | allowed | allowed | **PASS** | 17:16 |
| 3 | Read medicines (Item list) — Pharmacist | allowed | allowed | **PASS** | 17:16 |
| 4 | Read medicines (Item list) — Cashier | allowed | allowed | **PASS** | 17:16 |
| 5 | Read medicines (Item list) — Purchasing | allowed | allowed | **PASS** | 17:16 |
| 6 | Read medicines (Item list) — Inventory | allowed | allowed | **PASS** | 17:16 |
| 7 | Read medicines (Item list) — Accountant | allowed | allowed | **PASS** | 17:16 |
| 8 | Counter sale (open shift + checkout) — Owner | allowed | allowed | **PASS** | 17:16 |
| 9 | Counter sale (open shift + checkout) — Manager | allowed | allowed | **PASS** | 17:16 |
| 10 | Counter sale (open shift + checkout) — Pharmacist | allowed | allowed | **PASS** | 17:16 |
| 11 | Counter sale (open shift + checkout) — Cashier | allowed | allowed | **PASS** | 17:16 |
| 12 | Counter sale (open shift + checkout) — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 13 | Counter sale (open shift + checkout) — Inventory | refused | refused 403 | **PASS** | 17:16 |
| 14 | Counter sale (open shift + checkout) — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 15 | Void / cancel a sale directly — Owner | allowed | allowed | **PASS** | 17:16 |
| 16 | Void / cancel a sale directly — Manager | allowed | allowed | **PASS** | 17:16 |
| 17 | Void / cancel a sale directly — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 18 | Void / cancel a sale directly — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 19 | Void / cancel a sale directly — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 20 | Void / cancel a sale directly — Inventory | refused | refused 403 | **PASS** | 17:16 |
| 21 | Void / cancel a sale directly — Accountant | allowed | allowed | **PASS** | 17:16 |
| 22 | Add a medicine with a price — Owner | allowed | allowed | **PASS** | 17:16 |
| 23 | Add a medicine with a price — Manager | allowed | allowed | **PASS** | 17:16 |
| 24 | Add a medicine with a price — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 25 | Add a medicine with a price — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 26 | Add a medicine with a price — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 27 | Add a medicine with a price — Inventory | allowed | allowed | **PASS** | 17:16 |
| 28 | Add a medicine with a price — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 29 | See purchase cost (buying Item Price) — Owner | allowed | allowed | **PASS** | 17:16 |
| 30 | See purchase cost (buying Item Price) — Manager | allowed | allowed | **PASS** | 17:16 |
| 31 | See purchase cost (buying Item Price) — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 32 | See purchase cost (buying Item Price) — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 33 | See purchase cost (buying Item Price) — Purchasing | allowed | allowed | **PASS** | 17:16 |
| 34 | See purchase cost (buying Item Price) — Inventory | allowed | allowed | **PASS** | 17:16 |
| 35 | See purchase cost (buying Item Price) — Accountant | allowed | allowed | **PASS** | 17:16 |
| 36 | Create a supplier — Owner | allowed | allowed | **PASS** | 17:16 |
| 37 | Create a supplier — Manager | allowed | allowed | **PASS** | 17:16 |
| 38 | Create a supplier — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 39 | Create a supplier — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 40 | Create a supplier — Purchasing | allowed | allowed | **PASS** | 17:16 |
| 41 | Create a supplier — Inventory | refused | refused 403 | **PASS** | 17:16 |
| 42 | Create a supplier — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 43 | Create a purchase order — Owner | allowed | allowed | **PASS** | 17:16 |
| 44 | Create a purchase order — Manager | allowed | allowed | **PASS** | 17:16 |
| 45 | Create a purchase order — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 46 | Create a purchase order — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 47 | Create a purchase order — Purchasing | allowed | allowed | **PASS** | 17:16 |
| 48 | Create a purchase order — Inventory | allowed | allowed | **PASS** | 17:16 |
| 49 | Create a purchase order — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 50 | Receive stock (Purchase Receipt, batch + expiry) — Owner | allowed | allowed | **PASS** | 17:16 |
| 51 | Receive stock (Purchase Receipt, batch + expiry) — Manager | allowed | allowed | **PASS** | 17:16 |
| 52 | Receive stock (Purchase Receipt, batch + expiry) — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 53 | Receive stock (Purchase Receipt, batch + expiry) — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 54 | Receive stock (Purchase Receipt, batch + expiry) — Purchasing | allowed | allowed | **PASS** | 17:16 |
| 55 | Receive stock (Purchase Receipt, batch + expiry) — Inventory | allowed | allowed | **PASS** | 17:16 |
| 56 | Receive stock (Purchase Receipt, batch + expiry) — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 57 | Stock adjustment (Stock Reconciliation) — Owner | allowed | allowed | **PASS** | 17:16 |
| 58 | Stock adjustment (Stock Reconciliation) — Manager | allowed | allowed | **PASS** | 17:16 |
| 59 | Stock adjustment (Stock Reconciliation) — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 60 | Stock adjustment (Stock Reconciliation) — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 61 | Stock adjustment (Stock Reconciliation) — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 62 | Stock adjustment (Stock Reconciliation) — Inventory | allowed | allowed | **PASS** | 17:16 |
| 63 | Stock adjustment (Stock Reconciliation) — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 64 | Dispose of stock (write-off) — Owner | allowed | allowed | **PASS** | 17:16 |
| 65 | Dispose of stock (write-off) — Manager | allowed | allowed | **PASS** | 17:16 |
| 66 | Dispose of stock (write-off) — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 67 | Dispose of stock (write-off) — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 68 | Dispose of stock (write-off) — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 69 | Dispose of stock (write-off) — Inventory | allowed | allowed | **PASS** | 17:16 |
| 70 | Dispose of stock (write-off) — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 71 | Batches & Expiry page — Owner | allowed | allowed | **PASS** | 17:16 |
| 72 | Batches & Expiry page — Manager | allowed | allowed | **PASS** | 17:16 |
| 73 | Batches & Expiry page — Pharmacist | allowed | allowed | **PASS** | 17:16 |
| 74 | Batches & Expiry page — Cashier | allowed | allowed | **PASS** | 17:16 |
| 75 | Batches & Expiry page — Purchasing | allowed | allowed | **PASS** | 17:16 |
| 76 | Batches & Expiry page — Inventory | allowed | allowed | **PASS** | 17:16 |
| 77 | Batches & Expiry page — Accountant | allowed | allowed | **PASS** | 17:16 |
| 78 | Pharmacy Report — Owner | allowed | allowed | **PASS** | 17:16 |
| 79 | Pharmacy Report — Manager | allowed | allowed | **PASS** | 17:16 |
| 80 | Pharmacy Report — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 81 | Pharmacy Report — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 82 | Pharmacy Report — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 83 | Pharmacy Report — Inventory | refused | refused 403 | **PASS** | 17:16 |
| 84 | Pharmacy Report — Accountant | allowed | allowed | **PASS** | 17:16 |
| 85 | Read the void audit log — Owner | allowed | allowed | **PASS** | 17:16 |
| 86 | Read the void audit log — Manager | allowed | allowed | **PASS** | 17:16 |
| 87 | Read the void audit log — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 88 | Read the void audit log — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 89 | Read the void audit log — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 90 | Read the void audit log — Inventory | refused | refused 403 | **PASS** | 17:16 |
| 91 | Read the void audit log — Accountant | allowed | allowed | **PASS** | 17:16 |
| 92 | Read the general ledger — Owner | allowed | allowed | **PASS** | 17:16 |
| 93 | Read the general ledger — Manager | allowed | allowed | **PASS** | 17:16 |
| 94 | Read the general ledger — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 95 | Read the general ledger — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 96 | Read the general ledger — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 97 | Read the general ledger — Inventory | refused | refused 403 | **PASS** | 17:16 |
| 98 | Read the general ledger — Accountant | allowed | allowed | **PASS** | 17:16 |
| 99 | Add a staff account (User) — Owner | allowed | allowed | **PASS** | 17:16 |
| 100 | Add a staff account (User) — Manager | refused | refused 403 | **PASS** | 17:16 |
| 101 | Add a staff account (User) — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 102 | Add a staff account (User) — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 103 | Add a staff account (User) — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 104 | Add a staff account (User) — Inventory | refused | refused 403 | **PASS** | 17:16 |
| 105 | Add a staff account (User) — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 106 | Change PharmacyOS Settings — Owner | allowed | allowed | **PASS** | 17:16 |
| 107 | Change PharmacyOS Settings — Manager | refused | refused 403 | **PASS** | 17:16 |
| 108 | Change PharmacyOS Settings — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 109 | Change PharmacyOS Settings — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 110 | Change PharmacyOS Settings — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 111 | Change PharmacyOS Settings — Inventory | refused | refused 403 | **PASS** | 17:16 |
| 112 | Change PharmacyOS Settings — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 113 | Back up now — Owner | allowed | allowed | **PASS** | 17:16 |
| 114 | Back up now — Manager | refused | refused 403 | **PASS** | 17:16 |
| 115 | Back up now — Pharmacist | refused | refused 403 | **PASS** | 17:16 |
| 116 | Back up now — Cashier | refused | refused 403 | **PASS** | 17:16 |
| 117 | Back up now — Purchasing | refused | refused 403 | **PASS** | 17:16 |
| 118 | Back up now — Inventory | refused | refused 403 | **PASS** | 17:16 |
| 119 | Back up now — Accountant | refused | refused 403 | **PASS** | 17:16 |
| 120 | Guest cannot use the POS API | 403 | HTTP 403 | **PASS** | 17:16 |
| 121 | Guest opening /pos is sent to sign-in | redirect or login page | HTTP 200 | **PASS** | 17:16 |

### A15 Backup / restore

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | back up now (database + files, verified) | status ok with a backup folder | {"status": "ok", "folder": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-07/20-29-51"} | **PASS** | 17:29 |
| 2 | change after the backup | QA AFTER BACKUP exists before the restore | true | **PASS** | 17:29 |
| 3 | restore that backup | status restored | {"status": "restored", "folder": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-07/20-29-51"} | **PASS** | 17:30 |
| 4 | a safety backup was taken first | safety backup folder printed and present | ["/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-07/20-29-55"] | **PASS** | 17:30 |
| 5 | server answers again after the restore | sign-in works | owner@qa-pharmacy.test | **PASS** | 17:30 |
| 6 | QA BEFORE BACKUP is there | present | true | **PASS** | 17:30 |
| 7 | QA AFTER BACKUP is gone | absent | false | **PASS** | 17:30 |
| 8 | transactions consistent | the same sales as at backup time | {"sales": 98, "at_backup": 98} | **PASS** | 17:30 |
| 9 | inventory consistent (Bin = stock ledger) | no mismatch | all consistent | **PASS** | 17:30 |
| 10 | a planted backup folder name is refused | invalid_folder, nothing run | {"status": "invalid_folder"} | **PASS** | 17:30 |
| 11 | planted folders are not listed | 2099-01-01 not in the Backups list | false | **PASS** | 17:30 |
| 12 | a tampered backup is refused, data unchanged | failed_unchanged (exit 3); current data kept | {"status": "failed_unchanged", "folder": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-07/20-16-99"} | **PASS** | 17:30 |
| 13 | a restore that fails mid-way puts the safety backup back | failed_reverted; data as before; server answers | {"result": {"status": "failed_reverted", "folder": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-07/23-58"}, "marker_kept": true} | **PASS** | 17:31 |

### A16 Update / rollback

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | the same build again | status current, nothing done | {"status": "current", "version": "1.0.0rc2"} | **PASS** | 17:32 |
| 2 | a failing update rolls back | status rolled_back, previous version | {"status": "rolled_back", "version": "1.0.0rc2", "backup": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-07/20-32"} | **PASS** | 17:32 |
| 3 | version after rollback | 1.0.0rc2 / build 3311dd6cfc7d | ["1.0.0rc2", "3311dd6cfc7d"] | **PASS** | 17:32 |
| 4 | no pharmacy data lost | marker customer and every sale still there | {"marker": true, "sales": 98, "before": 98} | **PASS** | 17:32 |
| 5 | server answers, maintenance off after rollback | ping 200 and a sale screen context | HTTP 200 | **PASS** | 17:32 |
| 6 | one server operation at a time (a second update while one runs) | the second is refused as busy (exit 75); the first finishes (rolled back) | {"second_exit": 75, "second": {"status": "busy"}, "first": {"status": "rolled_back", "version": "1.0.0rc2", "backup": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-07/20-32-57"}} | **PASS** | 17:33 |

### A18 Offline

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | the server reaches the internet before the test | HTTP answers | 400 200 200 200 | **PASS** | 17:17 |
| 2 | internet cut for the pharmacy server (direct and through the proxy) | no answer from any site | 000 FAIL 000 FAIL 000 FAIL 000 FAIL | **PASS** | 17:17 |
| 3 | shift opens offline | an open shift | QA Counter cashier | **PASS** | 17:17 |
| 4 | barcode scan offline | Panadol found at once | {"items": ["QA-PAN500"], "ms": 64} | **PASS** | 17:17 |
| 5 | Arabic search offline | results | ["QA-PAN500", "QA-PANEXT"] | **PASS** | 17:17 |
| 6 | cash sale offline, Cloud unreachable | submitted without waiting for the Cloud | {"invoice": "ACC-SINV-2026-00094", "total": 5000.0, "change": 5000.0, "ms": 762} | **PASS** | 17:17 |
| 7 | receipt offline | receipt renders | HTTP 200, 8742 bytes | **PASS** | 17:17 |
| 8 | return offline | return submitted | ["ACC-SINV-2026-00095", -2500.0] | **PASS** | 17:17 |
| 9 | owner's report offline | answers | {"costs_visible": true, "has_sales": true} | **PASS** | 17:17 |
| 10 | Cloud events wait in the outbox (not lost, not sent, not blocking) | events queued, delivery tried and failed | {"events": 2, "sent": 0, "tried": 2, "error": "[unreachable] HTTPSConnectionPool(host='cloud-unreachable.pharmacyos-qa.example', port=443): Max retries exceeded with url: /integrations/erp/events (Caused by NameResolutionError(\"HTTPSConnection(host='cloud-unr | **PASS** | 17:20 |
| 11 | the Cloud failure is visible to the owner (PharmacyOS Settings → Cloud) | last error recorded | تعذّر الوصول إلى سحابة PharmacyOS: HTTPSConnectionPool(host='cloud-unreachable.pharmacyos-qa.example', port=443): Max retries exceeded with url: /integrations/e | **PASS** | 17:20 |
| 12 | shift closes offline | closing entry submitted | POS-CLO-2026-00002 | **PASS** | 17:20 |
| 13 | internet back after the test | HTTP answers again | 400 200 200 200 | **PASS** | 17:20 |

### A19 Barcode (simulated HID)

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | scan EAN of Panadol (first barcode) | Panadol found, barcode-scan mode | [["QA-PAN500", 100.0]] | **PASS** | 17:15 |
| 2 | scan the second barcode of the same medicine | also Panadol | ["QA-PAN500"] | **PASS** | 17:15 |
| 3 | unknown barcode | no item (the screen says not found) | {"items": [], "barcode_scan": false} | **PASS** | 17:15 |
| 4 | out-of-stock item scanned | found with 0 sellable | [["QA-INSULIN", 0]] | **PASS** | 17:15 |
| 5 | expired batch barcode scanned | batch recognised and flagged expired | [["QA-AMOXSYR", {"batch_id": "AMX-QA-2509", "expiry_date": "2026-10-01", "expired": true}]] | **PASS** | 17:15 |

### A20 Receipts (rendered)

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | 80 mm receipt of a counter sale | HTTP 200, 80 mm layout (no 58 mm rule) | HTTP 200, 9241 bytes, 58mm rule: False | **PASS** | 17:15 |
| 2 | Arabic and English medicine names on the receipt | both names printed | [["Panadol 500mg Tablets", null], ["Brufen 400mg Tablets", null]] | **PASS** | 17:15 |
| 3 | discount, cash received and change printed | line discount and change figures present | {"invoice": "ACC-SINV-2026-00005", "line_discounts": [0.0, 10.0], "paid": 10000.0, "change": 4800.0} | **PASS** | 17:15 |
| 4 | 58 mm receipt (PharmacyOS Settings → Receipt Paper Width) | narrow layout: max-width 58mm, @page 58mm | HTTP 200, @page 58mm: True | **PASS** | 17:15 |
| 5 | return (refund) receipt | renders, marked as a return | HTTP 200, 8742 bytes | **PASS** | 17:15 |

### A21 Security

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | cashier sells at 1 IQD through the document API | refused: the price must be the pharmacy's price | refused HTTP 403: Payment methods refreshed. Please review before proceeding. \| الصف 1: QA-PAN500: يجب أن يكون السعر سعر الصيدلية (2,500.000 د.ع). | **PASS** | 17:16 |
| 2 | cashier gives 100% off through the document API | refused: above the counter discount limit | refused HTTP 403: Payment methods refreshed. Please review before proceeding. \| خصم أكثر من 10% من قيمة البيع يحتاج إلى مدير الصيدلية. | **PASS** | 17:16 |
| 3 | cashier makes a credit (non-POS) invoice | refused: counter staff sell through the POS | refused HTTP 403: يبيع موظفو الكاونتر من خلال نقطة البيع، ضمن ورديتهم. | **PASS** | 17:16 |
| 4 | cashier back-dates a sale (expiry is checked against the posting date) | refused: today's date only | refused HTTP 403: تُسجَّل مبيعات ومرتجعات الكاونتر بتاريخ اليوم. | **PASS** | 17:16 |
| 5 | cashier sells on a counter that is not their open shift | refused: own shift only | refused HTTP 403: افتح ورديتك على Main Counter قبل البيع أو الإرجاع عليه. | **PASS** | 17:16 |
| 6 | cashier discount above the 10% ceiling at the POS | refused before payment (quote) | refused HTTP 403: الصف 1: الخصم أكثر من 10% يحتاج إلى مدير الصيدلية. | **PASS** | 17:16 |
| 7 | a correct counter sale still works (10% discount, own shift) | sale submitted for 2,250 | ["ACC-SINV-2026-00030", 2250.0] | **PASS** | 17:16 |
| 8 | a manager is not limited by the counter ceiling | 50% discount sale by the manager | ["ACC-SINV-2026-00031", 1250.0] | **PASS** | 17:16 |
| 9 | a customer's cheaper default price list at the counter | refused: the counter's price list | refused HTTP 403: تستخدم مبيعات الكاونتر قائمة أسعار الكاونتر (Standard Selling). | **PASS** | 17:16 |
| 10 | cashier cannot rewrite a sale's return ledger | ledger unchanged (and hidden from every client) | unchanged: {"returns":{"ACC-SINV-2026-00033":{"amounts":{"QA-PAN500":5000.0},"batches":{"QA-PAN500":{"PAN-QA-2601":2.0}},"items":{" | **PASS** | 17:16 |
| 11 | the same goods cannot be returned twice | second full return refused | refused HTTP 417: الصف # 1: لا يمكن الارجاع أكثر من 0.0 للبند QA-PAN500 | **PASS** | 17:16 |
| 12 | cashier never receives purchase cost | valuation_rate / last_purchase_rate absent | {"valuation_rate": null, "last_purchase_rate": null} | **PASS** | 17:16 |
| 13 | Redis refuses unauthenticated clients | NOAUTH | NOAUTH Authentication required. | **PASS** | 17:16 |
| 14 | guests cannot call the POS API | 403 | HTTP 403 | **PASS** | 17:16 |
| 15 | guests are sent to sign-in for the desk | login page / redirect | HTTP 200 | **PASS** | 17:16 |

### A22 Stress

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | create 120 products | all created | {"n": 120, "p50_ms": 58, "p95_ms": 83, "max_ms": 110} | **PASS** | 17:16 |
| 2 | one purchase receipt with 120 lines (120 batches) | submitted | {"ms": 9681} | **PASS** | 17:17 |
| 3 | catalogue size | ≥ 140 products | 143 | **PASS** | 17:17 |
| 4 | 100 rapid barcode scans | every scan resolves to its product | {"n": 100, "p50_ms": 31, "p95_ms": 42, "max_ms": 50, "wrong_or_missing": 0} | **PASS** | 17:17 |
| 5 | 100 searches (EN / AR / generic / partial) | every search returns results | {"n": 100, "p50_ms": 42, "p95_ms": 199, "max_ms": 213, "empty": 0} | **PASS** | 17:17 |
| 6 | 60 sequential sales | 60 distinct invoices | {"n": 60, "p50_ms": 358, "p95_ms": 453, "max_ms": 524, "invoices": 60} | **PASS** | 17:17 |
| 7 | stock after 60 sales | each of 20 products down by exactly 3 | {"QA-STRESS-000": 3.0, "QA-STRESS-001": 3.0, "QA-STRESS-002": 3.0, "QA-STRESS-003": 3.0, "QA-STRESS-004": 3.0, "QA-STRESS-005": 3.0, "QA-STRESS-006": 3.0, "QA-STRESS-007": 3.0, "QA-STRESS-008": 3.0, "QA-STRESS-009": 3.0, "QA-STRESS-010": 3.0, "QA-STRESS-011":  | **PASS** | 17:17 |
| 8 | stock = stock ledger for every QA product | no mismatch | all consistent | **PASS** | 17:17 |
| 9 | month report with populated data | answers in < 5 s | {"ms": 92} | **PASS** | 17:17 |
