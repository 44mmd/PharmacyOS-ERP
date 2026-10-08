# PharmacyOS Local ERP 1.0.0-rc.3 — final QA report

Release QA of the LOCAL ERP **1.0.0-rc.3**. The release changes the Arabic terminology (Iraqi usage): a cash /
POS shift is **«شِفت»**, plural **«شِفتات»**, masculine — display text only (`docs/RELEASE.md` → Release notes).
Two independent reviews of the first rc.3 pass (commit `0c99501`) found a translation leak, guard gaps, a
release-engineering gap (the installer's server bundle was not byte-for-byte the build QA had installed) and QA
checks that could not fail; all of them are fixed (*Issues found and fixed in this pass*). Because shipped code
and text changed again, the whole local QA suite was **run again**: on a server **installed from nothing** with
the final rc.3 bundle, and on a server installed with **1.0.0-rc.2**, given QA data and **updated to rc.3** the
way a pharmacy gets it. Everything below comes from the running product, driven over HTTP as its real users, plus
the app's own test suites and Windows CI. Nothing was mocked or edited. Where a test could not be physical
(Windows PC with WSL2, scanner, thermal printer, cash drawer) this report says so and claims nothing.

**Screenshots:** no longer maintained — the requirement was withdrawn by the product owner (see *Screenshots*
below). This report uses test results and code / runtime checks only.

## Environment

| | |
|---|---|
| Server | `install-server.sh` (what the Windows setup runs inside WSL) in a chroot of the **exact Ubuntu 24.04 WSL root filesystem** the setup imports (`ubuntu-wsl.tar.gz`, SHA-256 verified by the setup), on Linux x86-64, 4 CPUs. Test-only shims: this sandbox's proxy CA/env, `systemctl` → init scripts (a chroot has no systemd). |
| Software | PharmacyOS ERP **1.0.0rc3**, server build **`a701924350b1`** (bundle built by `npm run bundle` from the release commit's tree — the build the Windows installer carries, see A4); previous release **1.0.0rc2**, build `3311dd6cfc7d` (bundle built from commit `56f8266` in a temporary worktree); Frappe **16.36.1** (`97a5dd93`), ERPNext **16.37.0** (`af63cde4`); Python 3.14.6, Node 24.21.0, MariaDB 10.11, Redis (ACL password), nginx, supervisor. |
| Site | `pharmacy.local`, production mode, Arabic, Asia/Baghdad, IQD. |
| Clients | HTTP sessions per user (`qa/local/qalib.py`) on the pharmacy's clock (`TZ=Asia/Baghdad`: the server's business date was already 2026-10-09); the counter screen (`/pos`) and the desk (`/app`) fetched as the signed-in user for the Arabic they are served; Windows CI (`windows-latest`, Windows PowerShell 5.1) for the installer. |
| Runs (UTC, 2026-10-08) | **Fresh rc.3:** a new root 23:06; `install-server.sh` 23:07–23:14; the whole suite in Phase A's order 23:14–23:23 (install state, seed, operations, receipts, permissions, security, stress, offline, update / rollback, backup / restore, one operation at a time; `capture_screens.mjs` / `capture_desktop.mjs` not run). **Update rc.2 → rc.3:** a new root 23:24; rc.2 install 23:24–23:31; QA data on rc.2 23:31–23:32 (`qa_seed.py`, `qa_ops.py`: 105 rows, all PASS, describing rc.2 and kept out of this report); `pharmacyos-server update` with the rc.3 bundle 23:32:37–23:33:07 and its checks; then A16 and A15 again on the updated server until 23:36 (those rows carry the later times). Every step ran once. Raw results: `qa/local/results.json`. |

### Real vs simulated (read this first)

| Area | What was real | What was simulated / not tested |
|---|---|---|
| Server install | The real `install-server.sh` from an empty Ubuntu 24.04 WSL image (rc.3, and rc.2 for the update); production mode; translations compiled by the installer | WSL2 itself on a physical Windows PC (enablement, reboot-and-continue, boot task) |
| Server update | The real `pharmacyos-server update` from rc.2 to rc.3 with the rc.3 bundle in the installed app's `resources/server` — the command the desktop app's **Update now** runs; failed updates rolled back for real | The desktop app's update screens were not driven in rc.3 (they were captured with the withdrawn screenshot script); the offer logic is unit-tested (`localserver.test.js`: rc.3 offered over rc.2, never the reverse) |
| Windows setup | Real Windows (CI): installer built, installed silently, first launch, uninstalled with data kept; setup system check on real Windows | Install is never pressed on CI (CI cannot run WSL2) |
| Desktop app | Unit tests (`npm test`): 29 / 29 on Linux; on Windows CI 22 pass and 7 are skipped by design (6 need `bash` / `pwsh`, and on Windows `bash` may be WSL itself; 1 needs a POSIX shell — the PowerShell 5.1 parsing, BOM and helper checks run in their own Windows CI step); first launch on Windows CI | Startup / recovery / backup / About screens not re-run in rc.3: their code is unchanged since rc.2 (only the version moved) |
| Barcode scanning | The counter's scan lookup with real barcodes as the scanner delivers them (several barcodes per medicine, an unknown code, an out-of-stock item, an expired batch's barcode) and 100 rapid consecutive scans (A22), over HTTP as the cashier. **No keystroke test was run in rc.3:** the counter screen's keystroke handling (Enter / Tab suffix, fast scans) was exercised with simulated keystrokes in a browser in the rc.2 certification; the counter screen's code is unchanged since rc.2 | **No physical scanner was used. No scanner certification is claimed.** |
| Receipts | The real `PharmacyOS Receipt` print view as the server renders it, at 80 mm and 58 mm, for real counter sales and a return (Arabic/English, discount, change) | **No thermal printer was attached; no physical printing, paper cutting or cash-drawer kick is claimed.** |
| Offline | The server's internet really cut (firewall rules on its processes, direct and via proxy); Cloud address really unreachable | — |
| Multi-PC | Server answering on the LAN address | A second physical counter PC was not available |

## The terminology change, verified

| Where | How | Result |
|---|---|---|
| Repository (app sources, `locale/ar.po` msgstr, desktop, deploy, docs, QA) | `qa/check_terminology.py` (word-level, after normalising the Persian / Kurdish ی, ى and invisible joiners; `.bat` / `.cmd` included; colour phrases allowed; CI step *Arabic terminology*); ERPNext's 7 asset-depreciation / workstation shift strings listed as not a cash shift; `docs/qa/local/screenshots/` skipped as historical | clean |
| The app's suite | `tests/test_terminology.py` (6 tests): the rule (also as typed on other keyboards; the colour pink), the sources, every counter string with masculine agreement, the context strings, the generic words left as upstream serves them, the served Arabic with no old word for a cash shift | OK |
| Fresh install | A3: the counter screen (`/pos`) as served — الشِفت · فتح الشِفت · إغلاق الشِفت · لا يوجد شِفت مفتوح. افتح شِفتك لبدء البيع. · تم إغلاق الشِفت.; the desk's boot translations (15,136 strings) — الشِفتات · شِفتات نقطة البيع · لا شِفتات في هذه الفترة · فتح / إغلاق الشِفتات; the report's shift table فُتح / أُغلق / مفتوح and the counter's الشِفت **by context**, while the generic *Shift* / *Opened* / *Closed* / *Open* read what ERPNext and Frappe ship (يحول · افتتح · مغلق · فتح); no served string with the old word for a cash shift | PASS |
| Server messages | A10 *sale of a closed shift cannot be voided* (هذا البيع ضمن شِفت مغلق), A11 *a closed shift cannot be closed twice* (ليس لديك شِفت مفتوح.), A21 *own shift only* (افتح شِفتك على …) and *credit invoice* (… ضمن شِفتاتهم.) | PASS |
| Update rc.2 → rc.3 | A16: before the update the rc.2 counter (8 strings) and desk (19 strings) showed the old word; after it (translations recompiled by the update's `bench migrate`) the counter, the desk and the server's own messages read «شِفت», and the generic words are upstream's again | PASS |

## Results (final QA run)

**309 / 309 checks PASS** (0 FAIL).

| Workflow | Checks | PASS | FAIL | Automated tests |
|---|---|---|---|---|
| A3 Fresh install | 16 | 16 | 0 | install-command.test.js, setup.test.js, Windows CI, test_terminology (served Arabic) |
| A5 QA pharmacy | 4 | 4 | 0 | test_v1_certification (owner keeps the staff directory), test_local_journeys |
| A6 Purchasing | 11 | 11 | 0 | test_operations, test_local_journeys, test_v1_operations (supplier return), test_v1_certification (buyer receives with batch) |
| A7 Inventory | 8 | 8 | 0 | test_pharmacy_core, test_v1_operations (no re-dating) |
| A8 POS shift | 25 | 25 | 0 | test_web_pos, test_pos_shift, test_v1_certification (counter pricing) |
| A9 Returns | 6 | 6 | 0 | test_return_integrity_round4, test_web_pos |
| A10 Void | 19 | 19 | 0 | test_v1_operations (void, approver lock-out) |
| A11 Shift close | 5 | 5 | 0 | test_pos_close_shift |
| A12 Expired disposal | 12 | 12 | 0 | test_v1_operations (disposal) |
| A13 Reports | 10 | 10 | 0 | test_v1_operations (report), test_cost_exposure |
| A14 Permissions | 121 | 121 | 0 | test_permissions_matrix, test_cost_exposure, test_credit_note_authority |
| A15 Backup / restore | 13 | 13 | 0 | test_backup |
| A16 Update / rollback | 12 | 12 | 0 | desktop: localserver.test.js (versions, builds), test_terminology |
| A18 Offline | 13 | 13 | 0 | test_integration, test_sync (outbox retry and back-off) |
| A19 Barcode (simulated HID) | 5 | 5 | 0 | test_web_pos (scan) |
| A20 Receipts (rendered) | 5 | 5 | 0 | test_experience (receipt), test_local_journeys |
| A21 Security | 15 | 15 | 0 | test_v1_certification, test_remediation, test_cost_exposure |
| A22 Stress | 9 | 9 | 0 | test_concurrency_round4, test_return_concurrency |

## Issues found and fixed in this pass

P0 = data loss / security / unusable; P1 = major workflow broken; P2 = important with a workaround; P3 = polish.
The first seven rows come from the two reviews of the first rc.3 pass (commit `0c99501`); each was reproduced
before it was fixed.

| Sev | Issue (how it was found) | Fix | Re-test |
|---|---|---|---|
| P2 | **The installer's server bundle was not the build QA installed.** The Windows CI runner checks the repository out with CRLF line endings (`core.autocrlf`) and the bundle script turned only the five Linux scripts back to LF, so the rc.3 installer of run 37818158272 carried build `082e7758631f` (Python, `.po`, JSON, JS, `patches.txt`, `modules.txt` with CRLF) while every QA server ran `30705c5a83dd`. Converting the release tree to CRLF and bundling reproduced `082e7758631f` exactly; that bundle had never been installed on a server (review) | `.gitattributes`: PharmacyOS text files are checked out with LF on every OS (`text=auto`: images and fonts untouched); the CI `checks` job builds the bundle from the Linux checkout and the Windows job **fails** if its bundle's `BUILD_ID` differs; the build is printed in the job log and the run summary | A checkout with `core.autocrlf=true` now bundles to the Linux build (checked here); the release commit's Windows CI run (A4) |
| P3 | The counter's word for a cash shift leaked into ERPNext's asset-depreciation screens: the app overrode the generic msgid *Shift*, so the *Shift* field and column of the Depreciation Schedule and the asset's schedule table read «الشِفت» beside ERPNext's own asset-shift wording, contrary to `LOCALIZATION.md` / `FORK_PATCHES.md` (review) | The counter uses `_("Shift", context="POS")` with `msgctxt "POS"`; the generic override is gone, so ERPNext's *Shift* reads upstream's Arabic | `test_generic_words_keep_upstream_arabic`; the A3 desk row (generic *Shift* = upstream's, *Shift:POS* = الشِفت); A16 after the update |
| P3 | The report's shift-table words changed generic desk words everywhere: *Open* (e.g. Frappe's Deleted Document button) read «مفتوح» instead of «فتح», *Closed* order statuses read «أُغلق» instead of «مغلق», *Opened* «فُتح»; the first rc.3 test pinned those desk-wide values (pre-existing since rc.1; review) | `pharmacy_report.js` uses `__("Opened", null, "Shift table")`, `__("Closed", null, "Shift table")`, `__("Open", null, "Shift status")` with matching `msgctxt` entries; the generic overrides are gone; the test checks the context keys and that the generic words are upstream's | `test_terminology`; the A3 and A16 desk rows |
| P3 | Terminology guard gaps: the old word typed with the Persian / Kurdish ی (U+06CC) or split by a zero-width non-joiner passed; `.bat` / `.cmd` files were not scanned; the colour pink (the same letters) would have been flagged; a docs-only push did not start the CI guard (review) | The guard normalises text first (NFKC, ی / ى → ي, ZWNJ / ZWJ / word joiner / soft hyphen dropped), matches the feminine / pronoun / plural forms, allows colour phrases («… اللون», «اللون …») and the masculine colour word, reads `.bat` / `.cmd` (also UTF-16 and the Windows Arabic code page); push runs also start for `pharmacyos/docs/**`, `pharmacyos/qa/**` and `.gitattributes` | `test_guard_rule` (each case); guard clean on the repository |
| P3 | QA harness: rows that could not fail (A11 *voided sale not in expected cash*, A12 *expired batch blocked at the POS*, A13 *today* and *yesterday*; also A5 *owner signs in*, the A6 back-dated receipts, the A22 catalogue and 120-line receipt, A15 *server answers again after the restore*). Behind one of them the data looked wrong: the manager's back-dated desk sale had a grand total of **0** (rate 0, 4,500 paid, 4,500 change). Investigated on the QA server: the QA pharmacy's prices start on the day it was set up (an Item Price is valid from the day it is entered) and ERPNext prices a line by the invoice's posting date, so yesterday had no list price; managers have price authority, so the sale was accepted at 0. QA data, not a product defect (review) | Each of those rows now asserts the server's values: T9 cancelled, in the void log and excluded from the expected cash; a real counter checkout of the expired batch refused while it still holds 8; today's report equal to the day's invoices counted independently, with the closed shift (123,350, −500); yesterday's report 1 sale, gross = net = 4,500 and a Cash row of 4,500 (the harness types the price, as a manager does on the desk form); the closing entry lists exactly the shift's 11 sales and 1 return | The rows of A5, A6, A11, A12, A13, A15 and A22 below |
| P3 | The report and the checklist overstated two things: desktop tests "29 / 29 on Linux and Windows CI" (Windows CI ran 22 and skipped 7 by design) and a scanner "tested with simulated HID keystrokes" (that was rc.2; rc.3 tests the scan lookup over HTTP) (review) | Corrected here, in `RELEASE_CHECKLIST.md` and in `LOCAL_ERP_STATUS.md` | — |
| P3 | The first pass's incidents paragraph left out two incidents (review) | Completed below (*Test-environment incidents*) | — |
| P2 | (first rc.3 pass) After a **failed update rolled back**, the control script reported "rolled back" while the web workers were still restarting: the owner's next sign-in got the web server's "Sorry! We will be back soon" page (HTTP 502), so the desktop app's *Continue* could land on it (QA: A16) | `pharmacyos-server`: the rollback waits for the server to answer before reporting, like a restore and a successful update | A16 rollback rows and "one server operation at a time", on the fresh rc.3 server and again on the updated one |
| P3 | (first rc.3 pass) Test isolation: `test_expired_batch_cannot_be_redated_into_sellable_stock` reused a fixed batch; the shared helper `open_shift` reused a shift left open on an earlier day (ERPNext refuses every sale on an "outdated" opening entry) | A new batch per run; the helper closes such a shift the way the pharmacy does (POS Closing Entry) and opens today's | full suite |
| P3 | (first rc.3 pass) QA harness: the A20 check of Arabic and English medicine names read the Arabic name from the invoice line, where it is not stored | `qa_install.py receipts` reads each medicine's Arabic name (`Item.pharma_name_ar`) and requires both names in the rendered receipt | A20 rows |

**Remaining: P0 = 0, P1 = 0, P2 = 0.** Remaining P3 (documented, not blocking): upstream ERPNext desk strings
~26 % English, and ERPNext's generic *Shift* label (asset depreciation) shows upstream's own Arabic («يحول»); the
LAN share is plain HTTP inside the pharmacy network (firewall limited to the local subnet); the database root
password reaches `bench` as a command-line argument inside the WSL environment (root-only); held sales live in the
counter browser's storage until the end of the day; a pharmacy updating from rc.2 runs rc.2's control script for
that update, so the rollback wait above applies from rc.3 on; a sale recorded for a day before a medicine had a
price gets no list price from ERPNext (prices follow the posting date): the desk form shows 0 and the manager
types the price.

**Test-environment incidents (not product defects).** The QA machine is shared with other work.
*This pass:* the sandbox restarted between the passes, so its proxy port changed again and the test-only proxy
shim of the QA root was updated before the run; the first attempt to run the app's suite on the development bench
stopped with 31 errors *"Environment variable HTTPLIB2_CA_CERTS not a valid file"* (the sandbox's CA bundle path
is not readable by the bench user) and was run again with a readable copy of the same bundle: 266 tests, OK. The
QA servers' runs had no incident: every step ran once (their logs hold one attempt of each update and restore).
*First rc.3 pass (commit `0c99501`), complete:* the disk filled up **three** times — the first fresh rc.3 install
stopped at the database step (MariaDB: "No more room in record file") and was repeated from nothing; in the
suite, the A15 check *a restore that fails mid-way puts the safety backup back* found that the restore's safety
backup could not be written (the restore correctly refused and left the data unchanged, `failed_unchanged`), and
A15 was run again; after the update, the A15 tampered-backup step and *one server operation at a time* crashed in
the harness with ENOSPC and were run again (the interrupted A15 attempt's restore log,
`restore-20261008-172156.log`, preceded the rows that were kept). One fresh rc.3 install also failed because the
test-only proxy shim still named the sandbox's previous proxy port; the shim was updated and the install
repeated. Nothing in the product was changed for any of these.

The issues found and fixed in the 1.0.0-rc.2 certification (Phase A, commit `05a68fa`) are in that commit's
version of this report; every one of them is re-tested by the rows below.

## Automated tests

| Suite | Result | Where |
|---|---|---|
| PharmacyOS app (`bench run-tests --app pharmacyos_erp`) | **266 tests, OK** (313 s) on Frappe 16.36.1 / ERPNext 16.37.0 | development bench, release code |
| Desktop app (`npm test`) | **29 / 29** on Linux; Windows CI **22 pass, 7 skipped** by design (bash / pwsh / POSIX-shell tests), 0 fail | Linux (here and the CI `checks` job), Windows CI |
| Arabic terminology guard (`qa/check_terminology.py`) | clean | Linux and CI (`checks` job) |
| Windows CI (`.github/workflows/pharmacyos-desktop.yml`) | the push run of the release commit — see A4 | GitHub Actions |
| QA over HTTP (`qa/local/qa_*.py`) | **309 / 309 checks PASS** (tables above) | QA servers: fresh rc.3 install; fresh rc.2 install updated to rc.3 |

## A4 — Windows CI on the release commit

The push run of the release commit (the commit that adds this report), workflow *PharmacyOS Desktop
(Windows)*, runs the Linux checks (shell and PowerShell scripts parse, the **Arabic terminology guard**, desktop
unit tests and the **server bundle built from the Linux checkout**), then on `windows-latest`: the setup scripts
in Windows PowerShell 5.1, `npm test`, the build of `PharmacyOS-Setup-1.0.0-rc.3.exe`, a check that the server
bundle inside it has **the same `BUILD_ID` as the Linux one** (the job fails otherwise), a silent install
(installed server bundle checked), the first launch and a silent uninstall that keeps pharmacy data. The run
prints the installer's **file name, size in bytes, SHA-256** (`dist/SHA256SUMS.txt`), whether it is **signed**,
the commit and the **server bundle's version and build** — in the job log (job *Windows installer*, step
*Installer exists*) and in the run summary — so the release facts are readable without downloading the artifact
**PharmacyOS-Setup** (installer, `SHA256SUMS.txt` and the smoke run's images, kept 90 days). The build certified
above is `a701924350b1`; the run confirms the installer carries exactly that build. No code-signing certificate
is configured, so the installer is unsigned and the run says so; nothing is faked.

## A17 — server failure and recovery

Not re-run for rc.3: the desktop app's startup, recovery and restart screens were exercised in the rc.2
certification with the desktop capture script, which was withdrawn with the screenshots. The desktop app's code
is unchanged since rc.2 apart from its version; `npm test` covers the guards (a redirect away from the server
shows the recovery screen, never a blank page; privileged calls only from the app's own frames) and the local
server control (versions, builds, busy operations), and Windows CI launches the installed app. On the server
side, start / health / restore / update / rollback all ran for real in A15 and A16.

## Screenshots — withdrawn by the product owner

Screenshots are **no longer maintained** as QA evidence: the product owner withdrew the requirement for
1.0.0-rc.3. None were captured, re-captured or checked in this pass (`capture_screens.mjs` and
`capture_desktop.mjs` were not run; no screenshot evidence was published from CI). The images and
`screens.json` / `desktop-screens.json` in `docs/qa/local/screenshots/` are **historical 1.0.0-rc.2 captures**,
kept as they were taken: they show the wording of rc.2 (the older Arabic word for a shift) and are not evidence
for rc.3. The terminology guard skips that folder for this reason. Windows CI still opens the installed app and
walks the setup screens as part of its functional smoke test; those images stay inside the CI artifact and are
not used as evidence.

## A18 — offline (secondary PCs)

Single-PC mode is fully offline: the A18 rows ran a whole shift with the server's internet cut and the Cloud
unreachable. **Multi-PC limit (documented):** other counter PCs open the server over the LAN; when the server PC
is off or unreachable they show the recovery screen and cannot sell until it returns (v1 has no client-side
offline queue). This is a design limit, not a defect, and is in `RELEASE_CHECKLIST.md`.

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
| Core ERP workflows | **97 / 100** | Every A5–A13 workflow passed on a fresh rc.3 install as the real users, over HTTP, with the report and shift-close amounts asserted. Not 100: multi-PC selling was exercised over the server's address only, not from a second physical PC. |
| Data integrity | **97 / 100** | Stock equals the stock ledger for every product after 60+ sales; idempotent checkout; return bounds; void reversal; restore verified and reverted on failure; data kept across the rc.2 → rc.3 update and a failed update. Not 100: no power-cut test on a physical PC. |
| Security | **94 / 100** | P0 = P1 = P2 = 0; every exploit replayed and refused; 121-cell permission matrix as designed; Redis password; CSP on `/pos`. Remaining P3: plain HTTP on the pharmacy LAN, root password as a CLI argument inside WSL, unsigned installer. |
| Installer / update / recovery | **89 / 100** | Real install script from an empty Ubuntu 24.04 WSL image (rc.3, and rc.2 for the update); real update rc.2 → rc.3; failed updates rolled back and now answering when reported; restore; real Windows install/uninstall on CI, whose installer carries the QA'd server build (CI-enforced). Not higher: WSL2 enablement, reboot-and-continue and the boot task need a physical Windows 10/11 PC. |
| UI / UX | **91 / 100** | Arabic-first, RTL, IQD on every PharmacyOS screen; Iraqi terminology (الوجبة, «شِفت») enforced by a guard and tests and checked in the Arabic the server serves, without changing generic upstream words; keyboard-first counter. ~26 % of upstream ERPNext desk strings still English. Screens were not re-inspected visually in rc.3 (screenshots withdrawn); only display text changed. |
| Automated QA | **97 / 100** | 266 unit/integration tests, 29 desktop tests (all on Linux; 22 on Windows CI, 7 skipped there by design), 309 HTTP checks that assert the server's values, terminology guard, Windows CI. |
| Simulated hardware readiness | **90 / 100** | Scan lookups (several barcodes per medicine, batch barcodes, 100 rapid scans) and receipts at 80/58 mm verified as software in rc.3; the counter's keystroke handling (Enter/Tab) was verified with simulated keystrokes in the rc.2 browser run only (code unchanged since). |
| Physical hardware validation | **0 / 100** | No physical Windows PC, scanner, thermal printer or cash drawer was available. Nothing is claimed. |

**LOCAL SOFTWARE READINESS: 95 %**

**LOCAL PHYSICAL VALIDATION: 0 %** (needs the owner's devices: a Windows 10/11 PC, a scanner, 80/58 mm
printers, a cash drawer — see `RELEASE_CHECKLIST.md` items 21–24).

## Every check (expected / actual / result)

Rows from the fresh rc.3 server (23:14–23:23) except A15 and A16: A16 starts with the update from rc.2 (23:33) and both were then run again on the updated server (23:33–23:36), so their final rows are from that server. Times are UTC; business dates in the rows are the pharmacy's (Asia/Baghdad, 2026-10-09).

### A3 Fresh install

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | production mode (no developer mode, no test runner) | developer_mode and allow_tests off | {"developer_mode": 0, "allow_tests": null} | **PASS** | 23:14 |
| 2 | no sample data | 0 items, customers (besides walk-in), invoices, suppliers | {"items": 0, "customers": 1, "invoices": 0, "suppliers": 0} | **PASS** | 23:14 |
| 3 | one owner account, nobody else | owner@… only | ["owner@qa-pharmacy.test"] | **PASS** | 23:14 |
| 4 | the owner holds the Pharmacy Owner profile | Pharmacy Owner + System Manager | ["Accounts Manager", "Accounts User", "All", "Desk User", "Guest", "Inventory Manager", "Item Manager", "Pharmacy Accountant", "Pharmacy Manager", "Pharmacy Owner", "Purchase Manager", "Purchase User", "Purchasing Officer", "Report Manager", "Sales Manager", " | **PASS** | 23:14 |
| 5 | company, branch and warehouse created by the setup | one company, one branch with its warehouse | {"companies": ["QA TEST Pharmacy"], "branches": [{"name": "Main Branch", "pharmacyos_warehouse": "Main Branch - QTP"}]} | **PASS** | 23:14 |
| 6 | first counter ready (Main Counter, cash, receipt format) | POS Profile with warehouse, price list, PharmacyOS Receipt | [{"name": "Main Counter", "warehouse": "Main Branch - QTP", "print_format": "PharmacyOS Receipt", "selling_price_list": "Standard Selling"}] | **PASS** | 23:14 |
| 7 | password policy on, sign-up off | policy enabled, sign-up disabled | {"enable_password_policy": 1, "minimum_password_score": "2", "disable_signup": 1} | **PASS** | 23:14 |
| 8 | scheduler on (hourly backups) | enabled | true | **PASS** | 23:14 |
| 9 | no scheduled job waits in the future (time zone set at first run) | none | none | **PASS** | 23:14 |
| 10 | Arabic, Iraq time zone, IQD | ar / Asia/Baghdad / IQD | {"language": "ar", "time_zone": "Asia/Baghdad", "currency": "IQD"} | **PASS** | 23:14 |
| 11 | counter discount ceiling stored (10 %) | 10 | [["10"]] | **PASS** | 23:14 |
| 12 | the owner signs in and lands on the Pharmacy Dashboard | HTTP 200 | HTTP 200 | **PASS** | 23:14 |
| 13 | the counter's Arabic: a shift is «شِفت» (translations compiled by the installer) | الشِفت · فتح الشِفت · إغلاق الشِفت · لا يوجد شِفت مفتوح… · no older shift word on the screen | {"shift": "الشِفت", "open_shift": "فتح الشِفت", "close_shift": "إغلاق الشِفت", "no_shift": "لا يوجد شِفت مفتوح. افتح شِفتك لبدء البيع.", "shift_closed": "تم إغلاق الشِفت.", "old_word_left": 0} | **PASS** | 23:14 |
| 14 | the desk's Arabic (translations the server boots the desk with): shifts are «شِفتات» | الشِفتات · شِفتات نقطة البيع · لا شِفتات في هذه الفترة · the shift table's فُتح / أُغلق / مفتوح and the counter's الشِفت by context, while the generic Shift / Open / Closed / Opened keep Frappe's and ERPNext's Arabic; no served string uses the older word for a cash shift | {"Shifts": "الشِفتات", "No shifts in this period": "لا شِفتات في هذه الفترة", "POS sessions": "شِفتات نقطة البيع", "Opening Entries": "فتح الشِفتات", "Closing Entries": "إغلاق الشِفتات", "Opened:Shift table": "فُتح", "Closed:Shift table": "أُغلق", "Open:Shift  | **PASS** | 23:14 |
| 15 | self sign-up refused | refused (sign-up disabled) | HTTP 417 | **PASS** | 23:14 |
| 16 | all server programs running | web, socket.io, scheduler, 2 workers, 2 Redis | ["frappe-bench-redis:frappe-bench-redis-cache", "frappe-bench-redis:frappe-bench-redis-queue", "frappe-bench-web:frappe-bench-frappe-web", "frappe-bench-web:frappe-bench-node-socketio", "frappe-bench-workers:frappe-bench-frappe-long-worker-0", "frappe-bench-wo | **PASS** | 23:14 |

### A5 QA pharmacy

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | owner signs in | the owner created by setup can sign in | owner@qa-pharmacy.test | **PASS** | 23:14 |
| 2 | owner creates employee records linked to staff accounts | 7 employees linked to the staff users | [["QA Manager Omar", "manager@qa-pharmacy.test"], ["QA Pharmacist Huda", "pharmacist@qa-pharmacy.test"], ["QA Cashier Sara", "cashier@qa-pharmacy.test"], ["QA Cashier Ali", "cashier2@qa-pharmacy.test"], ["QA Purchasing Karim", "purchasing@qa-pharmacy.test"], [ | **PASS** | 23:14 |
| 3 | 20 medicines in 7 categories, batch + expiry tracked | 20 items, all has_batch_no and has_expiry_date | 20 items, groups=['Analgesics', 'Antibiotics', 'Chronic Care', 'Gastrointestinal', 'Personal Care', 'Respiratory & Allergy', 'Vitamins & Supplements'] | **PASS** | 23:14 |
| 4 | multiple barcodes on one medicine | Panadol 500 has 2 barcodes | ["6291100000011", "5000158062474"] | **PASS** | 23:14 |

### A6 Purchasing

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | supplier creation by the Purchasing Officer | 2 suppliers created by purchasing@ | [{"name": "QA Baghdad Pharma Supply", "owner": "purchasing@qa-pharmacy.test"}, {"name": "QA Basra Medical Trading", "owner": "purchasing@qa-pharmacy.test"}] | **PASS** | 23:14 |
| 2 | purchase order created and submitted by the Purchasing Officer | PO with 8 lines, submitted, owner purchasing@ | ["PUR-ORD-2026-00001", "purchasing@qa-pharmacy.test"] | **PASS** | 23:14 |
| 3 | partial receiving against the PO | PO partly received (0 < per_received < 100) | {"receipt": "MAT-PRE-2026-00001", "per_received": 41.891892, "status": "To Receive and Bill"} | **PASS** | 23:14 |
| 4 | full receiving completes the PO | per_received = 100, status To Bill | {"receipt": "MAT-PRE-2026-00002", "per_received": 100.0, "status": "To Bill"} | **PASS** | 23:14 |
| 5 | second supplier PO received in full | PO2 100% received | MAT-PRE-2026-00003 | **PASS** | 23:14 |
| 6 | back-dated delivery (now expired / near-expiry batches) + fresh batch | both receipts submitted (the first dated 200 days ago) | [["MAT-PRE-2026-00004", 1, "2026-03-23"], ["MAT-PRE-2026-00005", 1, "2026-10-09"]] | **PASS** | 23:14 |
| 7 | receiving an expired batch today is refused | server refuses an expired batch on today's receipt | refused HTTP 417: الصف رقم 1: انتهت صلاحية الوجبة AMX-QA-EXPIRED بالفعل. | **PASS** | 23:14 |
| 8 | purchase return to supplier takes stock out of the received batch | BRU-QA-2601 goes 50 → 48 | {"return": "MAT-PRE-2026-00006", "before": 50.0, "after": 48.0} | **PASS** | 23:14 |
| 9 | received cost becomes the stock valuation | incoming rate 1500 for Panadol | [{"incoming_rate": 1500.0, "actual_qty": 50.0, "valuation_rate": 1500.0}] | **PASS** | 23:14 |
| 10 | two batches of one medicine with different expiry | Panadol 50 + 50 in two batches | {"PAN-QA-2601": 50.0, "PAN-QA-2602": 50.0} | **PASS** | 23:14 |
| 11 | inventory after purchasing (Bin = received − returned) | Brufen 48, Panadol 100, Insulin none | {"QA-AUG1G": 30.0, "QA-PAN500": 100.0, "QA-PANEXT": 40.0, "QA-GLUCO500": 60.0, "QA-AZI500": 20.0, "QA-BRUF400": 48.0, "QA-ZYRTEC": 30.0, "QA-OMEP20": 40.0, "QA-CIPRO500": 3.0, "QA-SENSO": 12.0, "QA-VENTOLIN": 12.0, "QA-VITC1000": 30.0, "QA-CONCOR5": 25.0, "QA- | **PASS** | 23:14 |

### A7 Inventory

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | stock per batch (Batches & Expiry) | every received batch listed with qty and expiry | 22 batches; counts={'expired': 1, '30': 1, '60': 1, '90': 1, 'all': 22} | **PASS** | 23:14 |
| 2 | expired batch identified | AMX-QA-2509 flagged Expired | [["AMX-QA-2509", 8.0, "2026-10-03"]] | **PASS** | 23:14 |
| 3 | near-expiry batch identified | VOL-QA-2510 within 30 days | [["VOL-QA-2510", 25, "critical"]] | **PASS** | 23:14 |
| 4 | FEFO order | Augmentin: AUG-QA-2600 (earlier expiry) before AUG-QA-2601 | [["AUG-QA-2600", "2027-02-06"], ["AUG-QA-2601", "2027-08-05"]] | **PASS** | 23:14 |
| 5 | expired batch excluded from sellable stock | Amoxil sellable = AMX-QA-2604 only (12) | [["AMX-QA-2604", 12.0]] | **PASS** | 23:14 |
| 6 | low stock / out of stock (Inventory Health) | Cipro low (3 < 5), Insulin out of stock | {"rows": [{"item_code": "QA-INSULIN", "item_name": "Insulin Glargine Pen (cold chain)", "name_ar": "\u0642\u0644\u0645 \u0625\u0646\u0633\u0648\u0644\u064a\u0646 \u063a\u0644\u0627\u0631\u062c\u064a\u0646", "uom": "Nos", "is_medicine": 1, "qty": 0.0, "availabl | **PASS** | 23:14 |
| 7 | stock adjustment (count) by the Inventory Manager | Nivea 10 → 9, ledger entry written | {"reconciliation": "MAT-RECO-2026-00001", "before": 10.0, "after": 9.0} | **PASS** | 23:14 |
| 8 | cashier cannot adjust stock | Stock Reconciliation refused for a cashier | refused HTTP 403: لا يملك المستخدم cashier@qa-pharmacy.test حق الوصول إلى النمط عبر إذن دور للمستند جرد المخزون | **PASS** | 23:14 |

### A8 POS shift

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | open shift with 25,000 IQD | an open POS Opening Entry for the cashier | {"name": "POS-OPE-2026-00001", "company": "QA TEST Pharmacy", "pos_profile": "Main Counter", "period_start_date": "2026-10-09 02:14:59.923759"} | **PASS** | 23:15 |
| 2 | T1 scan + exact cash | 2 × 2,500 = 5,000 | ["ACC-SINV-2026-00001", 5000.0] | **PASS** | 23:15 |
| 3 | T2 item discount 10% + cash received 10,000 + change | 2,500 + 2,700 = 5,200; change 4,800 | ["ACC-SINV-2026-00002", 5200.0, 4800.0, 10000.0] | **PASS** | 23:15 |
| 4 | Arabic search | بنادول اكسترا finds Panadol Extra (أ/ا normalised) | ["QA-PANEXT"] | **PASS** | 23:15 |
| 5 | T3 transaction discount 5% | 7,000 − 5% = 6,650 | ["ACC-SINV-2026-00003", 6650.0, 350.0] | **PASS** | 23:15 |
| 6 | English search (partial) | omepra finds Omeprazole | ["QA-OMEP20"] | **PASS** | 23:15 |
| 7 | T4 quantity 3 | 3 × 3,500 = 10,500 | ["ACC-SINV-2026-00004", 10500.0] | **PASS** | 23:15 |
| 8 | T5 FEFO batch picked automatically | AUG-QA-2600 | [["QA-AUG1G", 1.0, "AUG-QA-2600"]] | **PASS** | 23:15 |
| 9 | T6 expired batch skipped automatically | AMX-QA-2604, never AMX-QA-2509 | [["QA-AMOXSYR", 1.0, "AMX-QA-2604"]] | **PASS** | 23:15 |
| 10 | T7 five-line sale, change from 40,000 | 5,500+8,000+4,500+6,500+6,000 = 30,500; change 9,500 | ["ACC-SINV-2026-00007", 30500.0, 9500.0] | **PASS** | 23:15 |
| 11 | T8 sale later partly returned | 4 × 4,000 + 2 × 5,500 = 27,000 | ["ACC-SINV-2026-00008", 27000.0] | **PASS** | 23:15 |
| 12 | T9 sale later voided | 2 × 4,000 = 8,000 | ["ACC-SINV-2026-00009", 8000.0] | **PASS** | 23:15 |
| 13 | T10 duplicate submission → one sale | the retry returns the same invoice; 1 invoice for the request | {"first": "ACC-SINV-2026-00010", "retry": "ACC-SINV-2026-00010", "replayed": true, "invoices": 1} | **PASS** | 23:15 |
| 14 | T11 cash 20,000 with change | 12,000; change 8,000 | ["ACC-SINV-2026-00011", 12000.0, 8000.0] | **PASS** | 23:15 |
| 15 | T12 scanned near-expiry batch sold | VOL-QA-2510 (not expired) sells | ACC-SINV-2026-00012 | **PASS** | 23:15 |
| 16 | overselling refused | Cipro qty 10 > 3 in stock | refused HTTP 417: For the item QA-CIPRO500, the Available qty 3.0 is less than the Required Qty 10.0 in the warehouse Main Branch - QTP. Please add sufficient qty in the warehouse. | **PASS** | 23:15 |
| 17 | out-of-stock item cannot be sold | Insulin (never received) | refused HTTP 417: Serial No / Batch No are mandatory for Item QA-INSULIN | **PASS** | 23:15 |
| 18 | selling the expired batch refused | AMX-QA-2509 refused at checkout | refused HTTP 417: الصف رقم 1: انتهت صلاحية الوجبة AMX-QA-2509 بالفعل. | **PASS** | 23:15 |
| 19 | price change refused on Main Counter | rate override refused (counter switch) | refused HTTP 403: تغيير السعر غير مسموح على هذا الكاونتر. | **PASS** | 23:15 |
| 20 | discount over 100% refused | discount 150% refused | refused HTTP 417: يجب أن يكون الخصم بين 0 و100%. | **PASS** | 23:15 |
| 21 | foreign payment method refused | a mode not on the counter is refused | refused HTTP 417: طريقة الدفع Wire Transfer غير متاحة على هذا الكاونتر. | **PASS** | 23:15 |
| 22 | sales history (own sales, newest first) | ≥ 12 sales listed | 12 | **PASS** | 23:15 |
| 23 | sales history search by invoice number | finds T8 | ["ACC-SINV-2026-00008"] | **PASS** | 23:15 |
| 24 | another cashier does not see these sales | cashier2 history excludes cashier's sales | 0 | **PASS** | 23:15 |
| 25 | receipt (reprint) of a sale | the PharmacyOS Receipt print view renders the sale | receipt with Panadol 500mg, batch and expiry | **PASS** | 23:15 |

### A9 Returns

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | partial return: 1 of 4 Glucophage, refund 4,000 | refund −4,000; return invoice submitted | ["ACC-SINV-2026-00013", -4000.0, -4000.0] | **PASS** | 23:15 |
| 2 | stock restored to the SAME batch | GLU-QA-2601 +1 | {"before": 56.0, "after": 57.0} | **PASS** | 23:15 |
| 3 | returnable quantity decreases | Glucophage 3 left, Zyrtec 2 | [["QA-GLUCO500", 3.0], ["QA-ZYRTEC", 2.0]] | **PASS** | 23:15 |
| 4 | cannot return more than sold | 4 more Glucophage refused (3 left) | refused HTTP 417: الصف # 1: لا يمكن الارجاع أكثر من 3.0 للبند QA-GLUCO500 | **PASS** | 23:15 |
| 5 | a return cannot be returned | return-of-return refused | refused HTTP 417: ACC-SINV-2026-00013 هي نفسها مرتجع. استخدم البيع الأصلي. | **PASS** | 23:15 |
| 6 | return audit: who / when / against | owner = cashier, return_against = T8 | ["cashier@qa-pharmacy.test", "2026-10-09 02:15:07.588262", "ACC-SINV-2026-00008"] | **PASS** | 23:15 |

### A10 Void

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | cashier cannot approve their own void | self-approval refused | refused HTTP 403: يجب أن يوافق شخص آخر على الإلغاء. | **PASS** | 23:15 |
| 2 | wrong manager password refused | refused, nothing changes | refused HTTP 403: رُفضت الموافقة: البريد أو كلمة المرور غير صحيحة. | **PASS** | 23:15 |
| 3 | another cashier cannot approve | cashier2 (no cancel right) refused as approver | refused HTTP 403: رُفضت الموافقة: البريد أو كلمة المرور غير صحيحة. | **PASS** | 23:15 |
| 4 | reason required | empty reason refused | refused HTTP 417: أدخل سبب الإلغاء. | **PASS** | 23:15 |
| 5 | cashier cannot void another cashier's sale | cashier2 asking to void cashier's sale refused | refused HTTP 403: يمكنك طلب إلغاء مبيعاتك فقط. | **PASS** | 23:15 |
| 6 | sale untouched after refusals | T9 still submitted (docstatus 1) | 1 | **PASS** | 23:15 |
| 7 | correct manager approval voids the sale | Void Log written, approved_by manager | {"name": "VOID-00001", "invoice": "ACC-SINV-2026-00009", "amount": 8000.0, "reason": "customer changed mind", "requested_by": "cashier@qa-pharmacy.test", "approved_by": "manager@qa-pharmacy.test", "voided_on": "2026-10-09 02:15:09.355964", "status": "voided"} | **PASS** | 23:15 |
| 8 | cashier stays signed in after the approval | get_context still answers as the cashier | cashier@qa-pharmacy.test | **PASS** | 23:15 |
| 9 | voided sale stays visible, marked voided | in history with voided = true | [{"name": "ACC-SINV-2026-00009", "customer_name": "زبون نقدي", "grand_total": 8000.0, "rounded_total": 8000.0, "currency": "IQD", "posting_date": "2026-10-09", "posting_time": "2:15:4.654393", "is_return": 0, "return_against": null, "status": "Cancelled", "doc | **PASS** | 23:15 |
| 10 | never deleted: invoice exists as Cancelled | docstatus 2 | {"docstatus": 2, "status": "Cancelled"} | **PASS** | 23:15 |
| 11 | stock reversed into the batch | VTC-QA-2601 +2 | {"before": 28.0, "after": 30.0} | **PASS** | 23:15 |
| 12 | financial correction: ledger entries cancelled | all GL entries of T9 is_cancelled = 1 | [{"account": "Stock In Hand - QTP", "debit": 0.0, "credit": 4800.0, "is_cancelled": 1}, {"account": "Stock In Hand - QTP", "debit": 4800.0, "credit": 0.0, "is_cancelled": 1}, {"account": "Debtors - QTP", "debit": 0.0, "credit": 8000.0, "is_cancelled": 1}, {"ac | **PASS** | 23:15 |
| 13 | audit log readable by the manager | 1 Void Log row with amount 8,000 | [{"name": "VOID-00001", "amount": 8000.0, "requested_by": "cashier@qa-pharmacy.test", "approved_by": "manager@qa-pharmacy.test", "reason": "customer changed mind", "voided_on": "2026-10-09 02:15:09.355964", "pos_profile": "Main Counter"}] | **PASS** | 23:15 |
| 14 | cashier cannot read the void log | PharmacyOS Void Log list refused | refused HTTP 403: عدم كفاية الإذن PharmacyOS Void Log | **PASS** | 23:15 |
| 15 | voiding twice is idempotent | returns the same void record | VOID-00001 | **PASS** | 23:15 |
| 16 | a returned sale cannot be voided | T8 (partly returned) refused | refused HTTP 417: أُرجعت مواد من هذا البيع، فلم يعد بالإمكان إلغاؤه. | **PASS** | 23:15 |
| 17 | repeated wrong approvals lock the requester out | 6th attempt (correct password) refused: too many failed approvals | refused HTTP 403: محاولات موافقة فاشلة كثيرة. انتظر بضع دقائق ثم أعد المحاولة. | **PASS** | 23:15 |
| 18 | previous-day sale cannot be voided | refused: use a return | refused HTTP 417: يمكن إلغاء مبيعات اليوم فقط. استخدم الإرجاع للمبيعات الأقدم. | **PASS** | 23:15 |
| 19 | sale of a closed shift cannot be voided | refused: use a return | refused HTTP 417: هذا البيع ضمن شِفت مغلق (POS-CLO-2026-00001). استخدم الإرجاع بدلًا من ذلك. | **PASS** | 23:15 |

### A11 Shift close

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | expected cash = 25,000 + takings − refunds (voided excluded) | 25,000 + 123,350 (sum of the shift's submitted sales and returns) | {"mode_of_payment": "Cash", "label": "نقد", "opening_amount": 25000.0, "expected_amount": 148350.0, "closing_amount": 0.0, "difference": 0.0} | **PASS** | 23:15 |
| 2 | voided sale not in expected cash | T9 (8,000, cancelled, in the void log) is not among the shift's sales and not counted: expected = 25,000 + takings, not + 8,000 more | {"expected_amount": 148350.0, "takings": 123350.0, "voided": [["ACC-SINV-2026-00009", 8000.0]], "in_void_log": ["ACC-SINV-2026-00009"]} | **PASS** | 23:15 |
| 3 | counted 500 short → variance −500 recorded | difference −500, closing entry submitted | {"name": "POS-CLO-2026-00001", "shift": "POS-OPE-2026-00001", "pos_profile": "Main Counter", "sales": 12, "grand_total": 123350.0, "net_total": 123350.0, "payments": [{"mode_of_payment": "Cash", "label": "نقد", "opening_amount": 25000.0, "expected_amount": 148 | **PASS** | 23:15 |
| 4 | a closed shift cannot be closed twice | second close refused | refused HTTP 417: ليس لديك شِفت مفتوح. | **PASS** | 23:15 |
| 5 | closing entry audit | owner = cashier, submitted, the shift's 12 invoices (11 sales — T1–T12 without the voided T9 — and 1 return), never the voided one | ["cashier@qa-pharmacy.test", 1, 12] | **PASS** | 23:15 |

### A12 Expired disposal

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | expired batch blocked at the POS | AMX-QA-2509 (8 in stock, expired) refused at a counter checkout | refused HTTP 417: الصف رقم 1: انتهت صلاحية الوجبة AMX-QA-2509 بالفعل. | **PASS** | 23:15 |
| 2 | cashier cannot dispose | disposal refused for a cashier | refused HTTP 403: غير مسموح لك بإتلاف المخزون. | **PASS** | 23:15 |
| 3 | cannot dispose more than the batch holds | 9 > 8 refused | refused HTTP 417: يوجد 8 فقط من الوجبة AMX-QA-2509 في Main Branch - QTP. | **PASS** | 23:15 |
| 4 | a healthy batch cannot be disposed as 'Expired' | AMX-QA-2604 refused | refused HTTP 417: الوجبة AMX-QA-2604 لم تنتهِ صلاحيتها بعد. اختر سببًا آخر إذا وجب إتلافها. | **PASS** | 23:15 |
| 5 | re-dating the expired batch refused (stock user) | expiry change refused | refused HTTP 403: انتهت صلاحية الوجبة AMX-QA-2509 في 03-10-2026. لا يمكن تغيير تاريخ وجبة منتهية الصلاحية: أتلفها بدلًا من ذلك. | **PASS** | 23:15 |
| 6 | re-dating the expired batch refused (manager) | expiry change refused | refused HTTP 403: انتهت صلاحية الوجبة AMX-QA-2509 في 03-10-2026. لا يمكن تغيير تاريخ وجبة منتهية الصلاحية: أتلفها بدلًا من ذلك. | **PASS** | 23:15 |
| 7 | re-dating the expired batch refused (owner) | an expired batch cannot be re-dated by anyone | refused HTTP 403: انتهت صلاحية الوجبة AMX-QA-2509 في 03-10-2026. لا يمكن تغيير تاريخ وجبة منتهية الصلاحية: أتلفها بدلًا من ذلك. | **PASS** | 23:15 |
| 8 | dispose the whole expired batch (8) | Stock Entry 'Expired Stock Disposal' submitted | {"name": "MAT-STE-2026-00001", "qty": 8.0, "batch_id": "AMX-QA-2509", "warehouse": "Main Branch - QTP", "reason": "Expired"} | **PASS** | 23:15 |
| 9 | stock deducted | AMX-QA-2509 → 0 | 0 | **PASS** | 23:15 |
| 10 | history: batch, qty, reason, user, time | row with reason Expired by stock@ | {"name": "MAT-STE-2026-00001", "posting_date": "2026-10-09", "posting_time": "2:15:13.605531", "owner": "stock@qa-pharmacy.test", "remarks": "إتلاف (منتهي الصلاحية) للوجبة AMX-QA-2509، انتهاء الصلاحية 2026-10-03. QA disposal", "reason": "Expired", "user": "QA  | **PASS** | 23:15 |
| 11 | cancelling a disposal never makes the batch sellable | units back as AMX-QA-2509 (expired); sellable list still excludes it; cancel recorded | {"back_in_stock": 8.0, "sellable": ["AMX-QA-2604"], "docstatus": 2} | **PASS** | 23:15 |
| 12 | disposed of again | batch back to 0 | 0 | **PASS** | 23:15 |

### A13 Reports

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | period presets | today, yesterday, 7 days, month | {"today": ["2026-10-09", "2026-10-09"], "yesterday": ["2026-10-08", "2026-10-08"], "week": ["2026-10-03", "2026-10-09"], "month": ["2026-10-01", "2026-10-09"]} | **PASS** | 23:15 |
| 2 | today: transactions, sales, returns, net | the day's submitted invoices counted independently (12 sales, 1 return, gross 129,850, net 125,850); the closed shift listed with its sales total 123,350 | {"sales": {"gross": 129850.0, "returns": 4000.0, "net": 125850.0, "transactions": 12, "returns_count": 1, "average": 10820.83}, "shift": {"shift": "POS-OPE-2026-00001", "user": "cashier@qa-pharmacy.test", "name": "QA Sara", "counter": "Main Counter", "opened": | **PASS** | 23:15 |
| 3 | discounts, voids, payment methods, top sellers, cashiers, shifts | sections present and filled | {"discounts": {"total": 650.0, "invoices": 2}, "voids": {"count": 1, "amount": 8000.0, "rows": [{"name": "VOID-00001", "invoice": "ACC-SINV-2026-00009", "amount": 8000.0, "reason": "customer changed mind", "requested_by": "cashier@qa-pharmacy.test", "approved_ | **PASS** | 23:15 |
| 4 | owner sees costs (purchases, stock value, profit) | costs_visible = 1 | true | **PASS** | 23:15 |
| 5 | manager: 7-day report | report returned | true | **PASS** | 23:15 |
| 6 | yesterday: the back-dated desk sale | 1 transaction, gross = net = 4,500 (Voltaren 50), a Cash payment row of 4,500 | {"sales": {"gross": 4500.0, "returns": 0.0, "net": 4500.0, "transactions": 1, "returns_count": 0, "average": 4500.0}, "payments": [{"mode_of_payment": "Cash", "label": "نقد", "amount": 4500.0, "count": 1}]} | **PASS** | 23:15 |
| 7 | month to date | report returned | true | **PASS** | 23:15 |
| 8 | custom range over 366 days refused | refused | refused HTTP 417: اختر فترة لا تتجاوز سنة واحدة. | **PASS** | 23:15 |
| 9 | cashier cannot open the report | 403 | refused HTTP 403: تقرير الصيدلية لصاحب الصيدلية والمدراء والمحاسب. | **PASS** | 23:15 |
| 10 | pharmacist cannot open the report | 403 | refused HTTP 403: تقرير الصيدلية لصاحب الصيدلية والمدراء والمحاسب. | **PASS** | 23:15 |

### A14 Permissions

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | Read medicines (Item list) — Owner | allowed | allowed | **PASS** | 23:15 |
| 2 | Read medicines (Item list) — Manager | allowed | allowed | **PASS** | 23:15 |
| 3 | Read medicines (Item list) — Pharmacist | allowed | allowed | **PASS** | 23:15 |
| 4 | Read medicines (Item list) — Cashier | allowed | allowed | **PASS** | 23:15 |
| 5 | Read medicines (Item list) — Purchasing | allowed | allowed | **PASS** | 23:15 |
| 6 | Read medicines (Item list) — Inventory | allowed | allowed | **PASS** | 23:15 |
| 7 | Read medicines (Item list) — Accountant | allowed | allowed | **PASS** | 23:15 |
| 8 | Counter sale (open shift + checkout) — Owner | allowed | allowed | **PASS** | 23:15 |
| 9 | Counter sale (open shift + checkout) — Manager | allowed | allowed | **PASS** | 23:15 |
| 10 | Counter sale (open shift + checkout) — Pharmacist | allowed | allowed | **PASS** | 23:15 |
| 11 | Counter sale (open shift + checkout) — Cashier | allowed | allowed | **PASS** | 23:15 |
| 12 | Counter sale (open shift + checkout) — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 13 | Counter sale (open shift + checkout) — Inventory | refused | refused 403 | **PASS** | 23:15 |
| 14 | Counter sale (open shift + checkout) — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 15 | Void / cancel a sale directly — Owner | allowed | allowed | **PASS** | 23:15 |
| 16 | Void / cancel a sale directly — Manager | allowed | allowed | **PASS** | 23:15 |
| 17 | Void / cancel a sale directly — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 18 | Void / cancel a sale directly — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 19 | Void / cancel a sale directly — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 20 | Void / cancel a sale directly — Inventory | refused | refused 403 | **PASS** | 23:15 |
| 21 | Void / cancel a sale directly — Accountant | allowed | allowed | **PASS** | 23:15 |
| 22 | Add a medicine with a price — Owner | allowed | allowed | **PASS** | 23:15 |
| 23 | Add a medicine with a price — Manager | allowed | allowed | **PASS** | 23:15 |
| 24 | Add a medicine with a price — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 25 | Add a medicine with a price — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 26 | Add a medicine with a price — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 27 | Add a medicine with a price — Inventory | allowed | allowed | **PASS** | 23:15 |
| 28 | Add a medicine with a price — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 29 | See purchase cost (buying Item Price) — Owner | allowed | allowed | **PASS** | 23:15 |
| 30 | See purchase cost (buying Item Price) — Manager | allowed | allowed | **PASS** | 23:15 |
| 31 | See purchase cost (buying Item Price) — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 32 | See purchase cost (buying Item Price) — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 33 | See purchase cost (buying Item Price) — Purchasing | allowed | allowed | **PASS** | 23:15 |
| 34 | See purchase cost (buying Item Price) — Inventory | allowed | allowed | **PASS** | 23:15 |
| 35 | See purchase cost (buying Item Price) — Accountant | allowed | allowed | **PASS** | 23:15 |
| 36 | Create a supplier — Owner | allowed | allowed | **PASS** | 23:15 |
| 37 | Create a supplier — Manager | allowed | allowed | **PASS** | 23:15 |
| 38 | Create a supplier — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 39 | Create a supplier — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 40 | Create a supplier — Purchasing | allowed | allowed | **PASS** | 23:15 |
| 41 | Create a supplier — Inventory | refused | refused 403 | **PASS** | 23:15 |
| 42 | Create a supplier — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 43 | Create a purchase order — Owner | allowed | allowed | **PASS** | 23:15 |
| 44 | Create a purchase order — Manager | allowed | allowed | **PASS** | 23:15 |
| 45 | Create a purchase order — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 46 | Create a purchase order — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 47 | Create a purchase order — Purchasing | allowed | allowed | **PASS** | 23:15 |
| 48 | Create a purchase order — Inventory | allowed | allowed | **PASS** | 23:15 |
| 49 | Create a purchase order — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 50 | Receive stock (Purchase Receipt, batch + expiry) — Owner | allowed | allowed | **PASS** | 23:15 |
| 51 | Receive stock (Purchase Receipt, batch + expiry) — Manager | allowed | allowed | **PASS** | 23:15 |
| 52 | Receive stock (Purchase Receipt, batch + expiry) — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 53 | Receive stock (Purchase Receipt, batch + expiry) — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 54 | Receive stock (Purchase Receipt, batch + expiry) — Purchasing | allowed | allowed | **PASS** | 23:15 |
| 55 | Receive stock (Purchase Receipt, batch + expiry) — Inventory | allowed | allowed | **PASS** | 23:15 |
| 56 | Receive stock (Purchase Receipt, batch + expiry) — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 57 | Stock adjustment (Stock Reconciliation) — Owner | allowed | allowed | **PASS** | 23:15 |
| 58 | Stock adjustment (Stock Reconciliation) — Manager | allowed | allowed | **PASS** | 23:15 |
| 59 | Stock adjustment (Stock Reconciliation) — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 60 | Stock adjustment (Stock Reconciliation) — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 61 | Stock adjustment (Stock Reconciliation) — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 62 | Stock adjustment (Stock Reconciliation) — Inventory | allowed | allowed | **PASS** | 23:15 |
| 63 | Stock adjustment (Stock Reconciliation) — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 64 | Dispose of stock (write-off) — Owner | allowed | allowed | **PASS** | 23:15 |
| 65 | Dispose of stock (write-off) — Manager | allowed | allowed | **PASS** | 23:15 |
| 66 | Dispose of stock (write-off) — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 67 | Dispose of stock (write-off) — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 68 | Dispose of stock (write-off) — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 69 | Dispose of stock (write-off) — Inventory | allowed | allowed | **PASS** | 23:15 |
| 70 | Dispose of stock (write-off) — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 71 | Batches & Expiry page — Owner | allowed | allowed | **PASS** | 23:15 |
| 72 | Batches & Expiry page — Manager | allowed | allowed | **PASS** | 23:15 |
| 73 | Batches & Expiry page — Pharmacist | allowed | allowed | **PASS** | 23:15 |
| 74 | Batches & Expiry page — Cashier | allowed | allowed | **PASS** | 23:15 |
| 75 | Batches & Expiry page — Purchasing | allowed | allowed | **PASS** | 23:15 |
| 76 | Batches & Expiry page — Inventory | allowed | allowed | **PASS** | 23:15 |
| 77 | Batches & Expiry page — Accountant | allowed | allowed | **PASS** | 23:15 |
| 78 | Pharmacy Report — Owner | allowed | allowed | **PASS** | 23:15 |
| 79 | Pharmacy Report — Manager | allowed | allowed | **PASS** | 23:15 |
| 80 | Pharmacy Report — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 81 | Pharmacy Report — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 82 | Pharmacy Report — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 83 | Pharmacy Report — Inventory | refused | refused 403 | **PASS** | 23:15 |
| 84 | Pharmacy Report — Accountant | allowed | allowed | **PASS** | 23:15 |
| 85 | Read the void audit log — Owner | allowed | allowed | **PASS** | 23:15 |
| 86 | Read the void audit log — Manager | allowed | allowed | **PASS** | 23:15 |
| 87 | Read the void audit log — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 88 | Read the void audit log — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 89 | Read the void audit log — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 90 | Read the void audit log — Inventory | refused | refused 403 | **PASS** | 23:15 |
| 91 | Read the void audit log — Accountant | allowed | allowed | **PASS** | 23:15 |
| 92 | Read the general ledger — Owner | allowed | allowed | **PASS** | 23:15 |
| 93 | Read the general ledger — Manager | allowed | allowed | **PASS** | 23:15 |
| 94 | Read the general ledger — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 95 | Read the general ledger — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 96 | Read the general ledger — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 97 | Read the general ledger — Inventory | refused | refused 403 | **PASS** | 23:15 |
| 98 | Read the general ledger — Accountant | allowed | allowed | **PASS** | 23:15 |
| 99 | Add a staff account (User) — Owner | allowed | allowed | **PASS** | 23:15 |
| 100 | Add a staff account (User) — Manager | refused | refused 403 | **PASS** | 23:15 |
| 101 | Add a staff account (User) — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 102 | Add a staff account (User) — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 103 | Add a staff account (User) — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 104 | Add a staff account (User) — Inventory | refused | refused 403 | **PASS** | 23:15 |
| 105 | Add a staff account (User) — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 106 | Change PharmacyOS Settings — Owner | allowed | allowed | **PASS** | 23:15 |
| 107 | Change PharmacyOS Settings — Manager | refused | refused 403 | **PASS** | 23:15 |
| 108 | Change PharmacyOS Settings — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 109 | Change PharmacyOS Settings — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 110 | Change PharmacyOS Settings — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 111 | Change PharmacyOS Settings — Inventory | refused | refused 403 | **PASS** | 23:15 |
| 112 | Change PharmacyOS Settings — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 113 | Back up now — Owner | allowed | allowed | **PASS** | 23:15 |
| 114 | Back up now — Manager | refused | refused 403 | **PASS** | 23:15 |
| 115 | Back up now — Pharmacist | refused | refused 403 | **PASS** | 23:15 |
| 116 | Back up now — Cashier | refused | refused 403 | **PASS** | 23:15 |
| 117 | Back up now — Purchasing | refused | refused 403 | **PASS** | 23:15 |
| 118 | Back up now — Inventory | refused | refused 403 | **PASS** | 23:15 |
| 119 | Back up now — Accountant | refused | refused 403 | **PASS** | 23:15 |
| 120 | Guest cannot use the POS API | 403 | HTTP 403 | **PASS** | 23:15 |
| 121 | Guest opening /pos is sent to sign-in | redirect or login page | HTTP 200 | **PASS** | 23:15 |

### A15 Backup / restore

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | back up now (database + files, verified) | status ok with a backup folder | {"status": "ok", "folder": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-09/02-34"} | **PASS** | 23:34 |
| 2 | change after the backup | QA AFTER BACKUP exists before the restore | true | **PASS** | 23:34 |
| 3 | restore that backup | status restored | {"status": "restored", "folder": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-09/02-34"} | **PASS** | 23:34 |
| 4 | a safety backup was taken first | safety backup folder printed and present | ["/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-09/02-34-08"] | **PASS** | 23:34 |
| 5 | server answers again after the restore | sign-in works, ping 200 | {"user": "owner@qa-pharmacy.test", "ping": 200} | **PASS** | 23:34 |
| 6 | QA BEFORE BACKUP is there | present | true | **PASS** | 23:34 |
| 7 | QA AFTER BACKUP is gone | absent | false | **PASS** | 23:34 |
| 8 | transactions consistent | the same sales as at backup time | {"sales": 15, "at_backup": 15} | **PASS** | 23:34 |
| 9 | inventory consistent (Bin = stock ledger) | no mismatch | all consistent | **PASS** | 23:34 |
| 10 | a planted backup folder name is refused | invalid_folder, nothing run | {"status": "invalid_folder"} | **PASS** | 23:34 |
| 11 | planted folders are not listed | 2099-01-01 not in the Backups list | false | **PASS** | 23:34 |
| 12 | a tampered backup is refused, data unchanged | failed_unchanged (exit 3); current data kept | {"status": "failed_unchanged", "folder": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-09/02-32-99"} | **PASS** | 23:34 |
| 13 | a restore that fails mid-way puts the safety backup back | failed_reverted; data as before; server answers | {"result": {"status": "failed_reverted", "folder": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-09/23-58"}, "marker_kept": true} | **PASS** | 23:35 |

### A16 Update / rollback

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | the update from the previous release (1.0.0rc2 → 1.0.0rc3): new installer's bundle, then Update now (pharmacyos-server update) | status updated, previous 1.0.0rc2, safety backup taken | {"status": "updated", "version": "1.0.0rc3", "previous": "1.0.0rc2", "backup": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-09/02-32"} | **PASS** | 23:33 |
| 2 | version and build after the update | 1.0.0rc3 / build a701924350b1 (the bundle the app carries) | ["1.0.0rc3", "a701924350b1"] | **PASS** | 23:33 |
| 3 | pharmacy data kept across the update | marker customer and every sale still there | {"marker": true, "sales": 15, "before": 15} | **PASS** | 23:33 |
| 4 | the counter's Arabic after the update: a shift is «شِفت» (recompiled during the update's migrate) | before: the older word; after: الشِفت · فتح الشِفت · إغلاق الشِفت …, no older word left | {"before": {"version": "1.0.0rc2", "older_word_on_counter": 8}, "after": {"shift": "الشِفت", "open_shift": "فتح الشِفت", "close_shift": "إغلاق الشِفت", "no_shift": "لا يوجد شِفت مفتوح. افتح شِفتك لبدء البيع.", "shift_closed": "تم إغلاق الشِفت.", "old_word_left | **PASS** | 23:33 |
| 5 | the desk's Arabic after the update (Pharmacy Report, sidebar): shifts are «شِفتات» | before: strings with the older word; after: الشِفتات · شِفتات نقطة البيع …, none left; the generic Shift / Open / Closed / Opened keep Frappe's and ERPNext's Arabic | {"before": {"strings_with_older_word": 19}, "after": {"Shifts": "الشِفتات", "No shifts in this period": "لا شِفتات في هذه الفترة", "POS sessions": "شِفتات نقطة البيع", "Opening Entries": "فتح الشِفتات", "Closing Entries": "إغلاق الشِفتات", "Opened:Shift table" | **PASS** | 23:33 |
| 6 | after the update the server's own messages use «شِفت» (cashier without an open shift) | refused: ليس لديك شِفت مفتوح. | refused HTTP 417: ليس لديك شِفت مفتوح. | **PASS** | 23:33 |
| 7 | the same build again | status current, nothing done | {"status": "current", "version": "1.0.0rc3"} | **PASS** | 23:33 |
| 8 | a failing update rolls back | status rolled_back, previous version | {"status": "rolled_back", "version": "1.0.0rc3", "backup": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-09/02-33"} | **PASS** | 23:33 |
| 9 | version after rollback | 1.0.0rc3 / build a701924350b1 | ["1.0.0rc3", "a701924350b1"] | **PASS** | 23:33 |
| 10 | no pharmacy data lost | marker customer and every sale still there | {"marker": true, "sales": 15, "before": 15} | **PASS** | 23:34 |
| 11 | server answers, maintenance off after rollback | ping 200 and a sale screen context | HTTP 200 | **PASS** | 23:34 |
| 12 | one server operation at a time (a second update while one runs) | the second is refused as busy (exit 75); the first finishes (rolled back) | {"second_exit": 75, "second": {"status": "busy"}, "first": {"status": "rolled_back", "version": "1.0.0rc3", "backup": "/mnt/c/ProgramData/PharmacyOS/Backups/2026-10-09/02-35-39"}} | **PASS** | 23:36 |

### A18 Offline

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | the server reaches the internet before the test | HTTP answers | 400 200 200 200 | **PASS** | 23:17 |
| 2 | internet cut for the pharmacy server (direct and through the proxy) | no answer from any site | 000 FAIL 000 FAIL 000 FAIL 000 FAIL | **PASS** | 23:17 |
| 3 | shift opens offline | an open shift | QA Counter cashier | **PASS** | 23:17 |
| 4 | barcode scan offline | Panadol found at once | {"items": ["QA-PAN500"], "ms": 61} | **PASS** | 23:17 |
| 5 | Arabic search offline | results | ["QA-PAN500", "QA-PANEXT"] | **PASS** | 23:17 |
| 6 | cash sale offline, Cloud unreachable | submitted without waiting for the Cloud | {"invoice": "ACC-SINV-2026-00091", "total": 5000.0, "change": 5000.0, "ms": 1046} | **PASS** | 23:17 |
| 7 | receipt offline | receipt renders | HTTP 200, 8742 bytes | **PASS** | 23:17 |
| 8 | return offline | return submitted | ["ACC-SINV-2026-00092", -2500.0] | **PASS** | 23:17 |
| 9 | owner's report offline | answers | {"costs_visible": true, "has_sales": true} | **PASS** | 23:17 |
| 10 | Cloud events wait in the outbox (not lost, not sent, not blocking) | events queued, delivery tried and failed | {"events": 2, "sent": 0, "tried": 2, "error": "[unreachable] HTTPSConnectionPool(host='cloud-unreachable.pharmacyos-qa.example', port=443): Max retries exceeded with url: /integrations/erp/events (Caused by NameResolutionError(\"HTTPSConnection(host='cloud-unr | **PASS** | 23:20 |
| 11 | the Cloud failure is visible to the owner (PharmacyOS Settings → Cloud) | last error recorded | تعذّر الوصول إلى سحابة PharmacyOS: HTTPSConnectionPool(host='cloud-unreachable.pharmacyos-qa.example', port=443): Max retries exceeded with url: /integrations/e | **PASS** | 23:20 |
| 12 | shift closes offline | closing entry submitted | POS-CLO-2026-00002 | **PASS** | 23:20 |
| 13 | internet back after the test | HTTP answers again | 400 200 200 200 | **PASS** | 23:20 |

### A19 Barcode (simulated HID)

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | scan EAN of Panadol (first barcode) | Panadol found, barcode-scan mode | [["QA-PAN500", 100.0]] | **PASS** | 23:15 |
| 2 | scan the second barcode of the same medicine | also Panadol | ["QA-PAN500"] | **PASS** | 23:15 |
| 3 | unknown barcode | no item (the screen says not found) | {"items": [], "barcode_scan": false} | **PASS** | 23:15 |
| 4 | out-of-stock item scanned | found with 0 sellable | [["QA-INSULIN", 0]] | **PASS** | 23:15 |
| 5 | expired batch barcode scanned | batch recognised and flagged expired | [["QA-AMOXSYR", {"batch_id": "AMX-QA-2509", "expiry_date": "2026-10-03", "expired": true}]] | **PASS** | 23:15 |

### A20 Receipts (rendered)

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | 80 mm receipt of a counter sale | HTTP 200, 80 mm layout (no 58 mm rule) | HTTP 200, 9241 bytes, 58mm rule: False | **PASS** | 23:15 |
| 2 | Arabic and English medicine names on the receipt | both names printed | [["Panadol 500mg Tablets", "بنادول 500 ملغ أقراص"], ["Brufen 400mg Tablets", "بروفين 400 ملغ أقراص"]] | **PASS** | 23:15 |
| 3 | discount, cash received and change printed | line discount and change figures present | {"invoice": "ACC-SINV-2026-00002", "line_discounts": [0.0, 10.0], "paid": 10000.0, "change": 4800.0} | **PASS** | 23:15 |
| 4 | 58 mm receipt (PharmacyOS Settings → Receipt Paper Width) | narrow layout: max-width 58mm, @page 58mm | HTTP 200, @page 58mm: True | **PASS** | 23:15 |
| 5 | return (refund) receipt | renders, marked as a return | HTTP 200, 8742 bytes | **PASS** | 23:15 |

### A21 Security

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | cashier sells at 1 IQD through the document API | refused: the price must be the pharmacy's price | refused HTTP 403: Payment methods refreshed. Please review before proceeding. \| الصف 1: QA-PAN500: يجب أن يكون السعر سعر الصيدلية (2,500.000 د.ع). | **PASS** | 23:16 |
| 2 | cashier gives 100% off through the document API | refused: above the counter discount limit | refused HTTP 403: Payment methods refreshed. Please review before proceeding. \| خصم أكثر من 10% من قيمة البيع يحتاج إلى مدير الصيدلية. | **PASS** | 23:16 |
| 3 | cashier makes a credit (non-POS) invoice | refused: counter staff sell through the POS | refused HTTP 403: يبيع موظفو الكاونتر من خلال نقطة البيع، ضمن شِفتاتهم. | **PASS** | 23:16 |
| 4 | cashier back-dates a sale (expiry is checked against the posting date) | refused: today's date only | refused HTTP 403: تُسجَّل مبيعات ومرتجعات الكاونتر بتاريخ اليوم. | **PASS** | 23:16 |
| 5 | cashier sells on a counter that is not their open shift | refused: own shift only | refused HTTP 403: افتح شِفتك على Main Counter قبل البيع أو الإرجاع عليه. | **PASS** | 23:16 |
| 6 | cashier discount above the 10% ceiling at the POS | refused before payment (quote) | refused HTTP 403: الصف 1: الخصم أكثر من 10% يحتاج إلى مدير الصيدلية. | **PASS** | 23:16 |
| 7 | a correct counter sale still works (10% discount, own shift) | sale submitted for 2,250 | ["ACC-SINV-2026-00027", 2250.0] | **PASS** | 23:16 |
| 8 | a manager is not limited by the counter ceiling | 50% discount sale by the manager | ["ACC-SINV-2026-00028", 1250.0] | **PASS** | 23:16 |
| 9 | a customer's cheaper default price list at the counter | refused: the counter's price list | refused HTTP 403: تستخدم مبيعات الكاونتر قائمة أسعار الكاونتر (Standard Selling). | **PASS** | 23:16 |
| 10 | cashier cannot rewrite a sale's return ledger | ledger unchanged (and hidden from every client) | unchanged: {"returns":{"ACC-SINV-2026-00030":{"amounts":{"QA-PAN500":5000.0},"batches":{"QA-PAN500":{"PAN-QA-2601":2.0}},"items":{" | **PASS** | 23:16 |
| 11 | the same goods cannot be returned twice | second full return refused | refused HTTP 417: الصف # 1: لا يمكن الارجاع أكثر من 0.0 للبند QA-PAN500 | **PASS** | 23:16 |
| 12 | cashier never receives purchase cost | valuation_rate / last_purchase_rate absent | {"valuation_rate": null, "last_purchase_rate": null} | **PASS** | 23:16 |
| 13 | Redis refuses unauthenticated clients | NOAUTH | NOAUTH Authentication required. | **PASS** | 23:16 |
| 14 | guests cannot call the POS API | 403 | HTTP 403 | **PASS** | 23:16 |
| 15 | guests are sent to sign-in for the desk | login page / redirect | HTTP 200 | **PASS** | 23:16 |

### A22 Stress

| # | Check | Expected | Actual (from the server) | Result | When |
|---|---|---|---|---|---|
| 1 | create 120 products | all 120 created | {"created": 120, "n": 120, "p50_ms": 58, "p95_ms": 81, "max_ms": 101} | **PASS** | 23:16 |
| 2 | one purchase receipt with 120 lines (120 batches) | submitted with 120 lines | {"ms": 9980, "docstatus": 1, "lines": 120} | **PASS** | 23:16 |
| 3 | catalogue size | ≥ 140 products | 143 | **PASS** | 23:16 |
| 4 | 100 rapid barcode scans | every scan resolves to its product | {"n": 100, "p50_ms": 34, "p95_ms": 53, "max_ms": 60, "wrong_or_missing": 0} | **PASS** | 23:16 |
| 5 | 100 searches (EN / AR / generic / partial) | every search returns results | {"n": 100, "p50_ms": 44, "p95_ms": 209, "max_ms": 258, "empty": 0} | **PASS** | 23:16 |
| 6 | 60 sequential sales | 60 distinct invoices | {"n": 60, "p50_ms": 350, "p95_ms": 438, "max_ms": 475, "invoices": 60} | **PASS** | 23:17 |
| 7 | stock after 60 sales | each of 20 products down by exactly 3 | {"QA-STRESS-000": 3.0, "QA-STRESS-001": 3.0, "QA-STRESS-002": 3.0, "QA-STRESS-003": 3.0, "QA-STRESS-004": 3.0, "QA-STRESS-005": 3.0, "QA-STRESS-006": 3.0, "QA-STRESS-007": 3.0, "QA-STRESS-008": 3.0, "QA-STRESS-009": 3.0, "QA-STRESS-010": 3.0, "QA-STRESS-011":  | **PASS** | 23:17 |
| 8 | stock = stock ledger for every QA product | no mismatch | all consistent | **PASS** | 23:17 |
| 9 | month report with populated data | answers in < 5 s | {"ms": 91} | **PASS** | 23:17 |
