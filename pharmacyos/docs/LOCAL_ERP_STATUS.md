# LOCAL ERP v1 — status map (October 2026, 1.0.0-rc.3)

**LOCAL ERP** = the standalone PharmacyOS ERP that runs at the pharmacy: a Frappe/ERPNext server (this fork's
`pharmacyos_erp` app on upstream Frappe 16.36.1 / ERPNext 16.37.0, MariaDB 10.11) inside a WSL2 environment on
the pharmacy's Windows PC (or a Linux server), opened through the **PharmacyOS ERP** desktop app (Electron) or a
browser. **CLOUD ERP** = the PharmacyOS Admin website (repository `PharmacyOS`). The CLOUD ↔ LOCAL sync engine
is not started, by decision; nothing in v1 makes it harder (one authoritative database per pharmacy, every
document has a stable name, the existing outbox is untouched).

Evidence labels:
* **verified** — run in October 2026 on Frappe 16.36.1 / ERPNext 16.37.0; test module or run named;
* **CI** — run by GitHub Actions on `windows-latest` (Windows Server 2025, Windows PowerShell 5.1);
* **code** — in the source and the repository's tests, ERPNext standard behaviour;
* **hardware** — needs a physical Windows PC, scanner or printer (production validation).

## v1 scope

In: counter sales (scan, search, discounts, payment, receipt, hold, returns, voids), cash shifts, stock with
batches and expiry (FEFO, expired never sold, expired disposal), purchasing (suppliers, orders, receipts,
supplier returns), reports, users and roles with server-side permissions and cost privacy, Arabic-first UI,
offline operation, backups and restore, a one-file Windows installer that installs the server, start-up and
recovery, guided updates with rollback.

Out (future modules): prescriptions, controlled-drug register, payroll/attendance (HRMS), GS1 DataMatrix,
Iraqi chart-of-accounts template, CLOUD sync, a client-side offline queue for counter PCs that lose the LAN
server.

## Sales / POS (`/pos`)

| Feature | Status | Evidence |
|---|---|---|
| Barcode scanning (HID, Enter or Tab suffix, fast queued scans, batch barcodes) | COMPLETE | verified (`test_web_pos`, browser: 3 rapid scans → qty 3) |
| Search (name, Arabic name, generic), cart, quantities, stock warnings | COMPLETE | verified |
| Pricing, taxes, discounts (per-counter switches enforced on the server) | COMPLETE | verified (`test_web_pos`) |
| Payment, change, idempotent checkout | COMPLETE | verified |
| Receipts (80 mm and 58 mm, Arabic/English, batch/expiry per line) | COMPLETE in software | verified (render); printer = hardware |
| Hold / resume | COMPLETE | verified (browser) |
| Returns (root-sale bounds, refunds, concurrency) | COMPLETE | verified (`test_return_integrity_round4`, browser) |
| Sales history with search | COMPLETE | verified (browser) |
| Void with manager approval + audit log, never deleted | COMPLETE | verified (`test_v1_operations`, browser) |
| Cash shifts: open with float, close with counted cash and difference | COMPLETE | verified (`test_pos_shift`, `test_pos_close_shift`, browser: float 25,000, variance −500) |
| A whole shift without the Desk | COMPLETE | verified (browser walkthrough) |
| FEFO, expired batches never sold | COMPLETE | verified (`test_pharmacy_core`, `test_web_pos`) |

## Inventory

| Feature | Status | Evidence |
|---|---|---|
| Stock, ledger, adjustments, counts, transfers | COMPLETE | verified / code (ERPNext) |
| Batches & expiry page, low stock, reorder suggestions | COMPLETE | verified (`test_pharmacy_core`) |
| Expired-stock disposal (batch → qty → reason → write-off → history, user and time) | COMPLETE | verified (`test_v1_operations`, browser) |
| Expired stock cannot return to sellable stock (no re-dating by staff, never sold) | COMPLETE | verified (`test_v1_operations`) |

## Purchasing

| Feature | Status | Evidence |
|---|---|---|
| Suppliers (owner/manager/purchasing can create) | COMPLETE | verified (browser; `test_permissions_matrix`) |
| Purchase orders, receipts with batch + expiry rules, cost updates | COMPLETE | verified for receipts (`test_operations`, `test_local_journeys`); code for orders (ERPNext) |
| Supplier returns take stock out of the batch | COMPLETE | verified (`test_v1_operations`) |

## Reports

| Feature | Status | Evidence |
|---|---|---|
| Pharmacy Report: today/yesterday/7 days/month/custom; sales, returns, net, transactions, discounts, voids, payment methods, top items, by cashier, shift summaries | COMPLETE | verified (`test_v1_operations`, browser) |
| Purchases, stock value, gross profit — owners/accountants only | COMPLETE | verified (cost-gated) |
| Low / expiring / expired stock | COMPLETE | verified (Batches & Expiry, Inventory Health) |
| Cashiers: no report, no cost data | COMPLETE | verified (403; `test_cost_exposure`) |

## People and rights

Users and role profiles (Owner, Manager, Pharmacist, Cashier, Inventory, Purchasing, Accountant) enforced on
the server: COMPLETE, verified (`test_permissions_matrix`, `test_cost_exposure`). Arabic UI: COMPLETE for
PharmacyOS screens, verified (`test_localization`; Iraqi terms: الوجبة for a batch, «شِفت» / «شِفتات» for a cash
shift since 1.0.0-rc.3 — `test_terminology`, `qa/check_terminology.py` in CI); ~26 % of upstream ERPNext strings
remain English.

## Platform and installation

| Area | Status | Evidence |
|---|---|---|
| One Setup.exe (desktop + server bundle), version 1.0.0-rc.3 | COMPLETE | CI (build, silent install, first launch, uninstall keeps data) |
| Setup screens: pharmacy details, system check, elevated install, progress | COMPLETE | verified (Electron/Playwright); system check CI |
| WSL2 + distro + server install, reboot and continue | COMPLETE in code | server part verified on a fresh Ubuntu 24.04 WSL image; WSL2 on Windows = hardware |
| Server auto-start (scheduled task), startup screen, recovery | COMPLETE in code | verified (Electron); boot task = hardware |
| Offline operation | COMPLETE | verified (A18: the server's internet cut by firewall, Cloud unreachable — a full shift sold, returned, printed and closed; Cloud events waited in the outbox) |
| Backups (hourly/daily, verified), restore from the app | COMPLETE | verified (`test_backup`, app restore round trip on the fresh server); scheduled jobs recover when the clock is put back (`TestSchedulerClock`) |
| Server update with automatic rollback | COMPLETE | verified (a real update 1.0.0-rc.2 → 1.0.0-rc.3 with QA data kept; a real failure rolled back) |
| Desktop update over the old version, data kept | COMPLETE | code (NSIS upgrade; uninstall-on-upgrade skips the server) |
| Reproducible build, CI artifact, draft-only releases | COMPLETE | CI |
| Code signing | READY, not signed | hooks in CI/package.json; the certificate is an external dependency |
| Scanners / thermal printers / cash drawer | COMPLETE in software, UNKNOWN on hardware | test receipt in the app; hardware |

## Verified in this pass (release QA, 1.0.0-rc.3)

The full evidence is `docs/qa/local/FINAL_QA_REPORT.md` (every check: expected, actual, PASS/FAIL, automated
test) and `docs/qa/local/RELEASE_CHECKLIST.md`. 1.0.0-rc.3 changes the Arabic for a cash/POS shift to «شِفت» /
«شِفتات» (display text only); because shipped text changed, everything below was run again.

* Full PharmacyOS suite on Frappe 16.36.1 / ERPNext 16.37.0: **265 tests, OK** (including
  `test_terminology`); 309 / 309 QA checks over HTTP.
* Desktop unit tests 29 / 29 (Linux and Windows CI); the Arabic terminology guard (`qa/check_terminology.py`) in CI.
* A QA pharmacy on a server installed from nothing (rc.3 bundle) in the Ubuntu 24.04 WSL image: owner,
  manager, pharmacist, cashier, purchasing, inventory and accountant accounts; two suppliers; 20 medicines with
  categories, batches, expired / near-expiry / low / out-of-stock items and several barcodes; then purchasing,
  inventory, a 25,000 IQD shift with ≥ 10 sales, returns, voids, a −500 close, disposal, reports, permissions,
  backup / restore, update / rollback, offline, barcode lookups, security exploits replayed and stress — all
  over HTTP as the real users (`qa/local/*.py`). The installer's compiled Arabic was checked as served
  (the counter screen and the desk).
* A real update **1.0.0-rc.2 → 1.0.0-rc.3**: a server installed from the rc.2 bundle (rebuilt from commit
  `56f8266`, same build id as certified), given QA data, updated with the rc.3 bundle by `pharmacyos-server
  update` (what the app's *Update now* runs): data kept, version/build exact, the Arabic recompiled and served
  with «شِفت»; then the failed-update rollback and restore checks again on the updated server.
* Screenshots are no longer maintained (requirement withdrawn by the product owner); the images in
  `docs/qa/local/screenshots/` are historical 1.0.0-rc.2 captures.
* Windows CI on the release commit: PowerShell 5.1, build, silent install, first launch, uninstall keeps data;
  the installer's file name, size, SHA-256 and signing state printed in the job log and the run summary.

## Production validation still needed (not code)

1. Install on physical Windows 10 and 11 PCs: WSL2 enablement, reboot-and-continue, boot task, port 80.
2. A code-signing certificate (SmartScreen).
3. Real barcode scanners, 80/58 mm thermal printers, a cash drawer on the printer.
4. A pilot pharmacy running full days.
