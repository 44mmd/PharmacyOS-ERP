# LOCAL ERP — status map (October 2026)

**LOCAL ERP** = the standalone PharmacyOS ERP that runs at the pharmacy: a Frappe/ERPNext server (this
fork's `pharmacyos_erp` app on upstream Frappe 16.36.1 / ERPNext 16.37.0, MariaDB) on the pharmacy's own PC
(WSL2) or LAN server, opened through the **PharmacyOS ERP** Windows desktop app (Electron, `desktop/`) or any
browser. **CLOUD ERP** = the PharmacyOS Admin website (repository `PharmacyOS`). They coexist; a future sync
engine will connect them. The existing Cloud integration (outbox → Cloud, website orders pulled from the
Cloud) is listed below but was not extended.

How each line was established:
* **verified** — run in October 2026 on a fresh Frappe 16.36.1 / ERPNext 16.37.0 site (MariaDB 10.11), test
  modules named;
* **code** — read in the source, covered by the repository's own tests or docs, not re-run this time;
* **untested** — written, never run on its target.

| Status | Meaning |
|---|---|
| COMPLETE | works end-to-end |
| PARTIAL | exists, with important missing states |
| BROKEN | implemented but not usable |
| MISSING | does not exist |
| UNKNOWN | cannot be determined without hardware or a Windows machine |

## Sales / POS (`/pos`, `pos/api.py`, `public/js/pos/pos.js`)

| Feature | Status | Evidence |
|---|---|---|
| Barcode scanning (keyboard wedge, queued scans, batch barcodes) | COMPLETE | verified (`test_web_pos`, browser run) |
| Product search (name, Arabic name, generic) | COMPLETE | verified (browser run) |
| Cart, quantities, stock warnings | COMPLETE | verified |
| Pricing (ERPNext price lists, pricing rules, taxes) | COMPLETE | verified (`test_web_pos.test_quote…`) |
| Discounts (line %, sale %, per-counter switches enforced on the server) | COMPLETE | verified (`test_counter_switches…`) |
| Payment, change, idempotent checkout | COMPLETE | verified (`test_checkout_is_idempotent…`) |
| Receipts (80 mm `PharmacyOS Receipt`, A4 invoice, batch/expiry per line) | COMPLETE | verified (`test_local_journeys` renders the receipt) |
| Returns (root-sale, quantity and money bounds, concurrency) | COMPLETE | verified (`test_return_integrity_round4`, 19 tests; `test_local_journeys`) |
| Voids (cancel a sale) | PARTIAL | code: managers/accountant cancel in the desk (stock, batch, GL reversed); not on the `/pos` screen |
| Held sales (hold / resume) | COMPLETE *(new)* | verified (browser run); per computer, no stock reservation |
| Cashier assignment | COMPLETE | verified: every invoice is owned by the signed-in cashier; counters can be limited to users |
| Cash shifts (open / sell / count / close) | COMPLETE *(close at the counter new)* | verified (`test_pos_shift`, `test_pos_close_shift`, browser run) |
| Customers at the counter (walk-in, search, quick add) | COMPLETE | code |
| FEFO + expired batches never sold | COMPLETE | verified (`test_pharmacy_core`, `test_web_pos`) |

## Inventory

| Feature | Status | Evidence |
|---|---|---|
| Stock per warehouse, stock ledger (movements) | COMPLETE | verified (ledger checked in `test_flows`, `test_local_journeys`) |
| Adjustments / counts / transfers | COMPLETE | code (ERPNext Stock Entry, Stock Reconciliation) |
| Expiry, batches (الوجبة), Batches & Expiry page | COMPLETE | verified (`test_pharmacy_core`) |
| Low stock (Inventory Health, Reorder Suggestions) | COMPLETE | verified (`test_pharmacy_core`) |
| Last-unit protection, website reservations | COMPLETE | code (`test_concurrency_round4`, not re-run) |
| Expired-stock quarantine / disposal workflow | MISSING | `PRODUCT.md` known gaps |

## Medicines / products

Creation (Add Medicine dialog), editing, barcode, selling price, cost (hidden from counter staff), expiry
(batch), stock, categories (Item Group), brands, active ingredients, dosage forms: **COMPLETE** (code;
`test_foundation`, `test_cost_exposure` verified for cost privacy).

## Purchasing

| Feature | Status | Evidence |
|---|---|---|
| Suppliers, purchase orders | COMPLETE | code (ERPNext) |
| Purchase receipts with batch + expiry rules | COMPLETE | verified (`test_operations`: expired rejected, short shelf life warns/blocks) |
| Receiving → stock → sale | COMPLETE | verified (`test_local_journeys`, new) |
| Cost updates (valuation) | COMPLETE | code (ERPNext FIFO/moving average) |
| Purchase returns (supplier returns / debit notes) | COMPLETE | code (ERPNext; in the Purchasing sidebar) — no PharmacyOS-specific rule |

## People, rights, reports, settings

| Area | Status | Evidence |
|---|---|---|
| Customers | COMPLETE | code (ERPNext Customer) |
| Users and role profiles (Owner, Manager, Pharmacist, Cashier, Inventory, Purchasing, Accountant, Integration) | COMPLETE | verified (`test_permissions_matrix`) |
| Employees (records) | COMPLETE | code (ERPNext Employee, Team sidebar) |
| Payroll / attendance / leave | MISSING | needs the HRMS app (not installed) |
| Cost privacy for counter staff | COMPLETE | verified (`test_cost_exposure`) |
| Reports / analytics (ERPNext reports, dashboard, expiry intelligence) | COMPLETE | code; dashboard verified (`test_pharmacy_core`) |
| Settings (PharmacyOS Settings) | COMPLETE | code |
| Arabic-first UI | COMPLETE for PharmacyOS screens; ~26 % of upstream ERPNext strings English | verified (`test_localization`) |

## Platform

| Area | Status | Evidence |
|---|---|---|
| Database | COMPLETE | MariaDB 10.11, one authoritative database per pharmacy |
| Backup (hourly/daily, verified, encrypted option) / restore script | COMPLETE | code (`test_backup`; restore tested on Linux per `DEPLOYMENT.md`) |
| Offline operation (no internet) | COMPLETE by architecture | the server is on the pharmacy's PC/LAN; every journey above ran with no Cloud configured. A client PC that loses the *LAN server* cannot sell (offline banner, no client-side queue) |
| Printing | COMPLETE in software; UNKNOWN on real thermal printers | Electron silent printing tested on Linux; no hardware test |
| Desktop app (Electron) | COMPLETE on Linux; UNKNOWN on Windows | unit tests pass; never run on Windows |
| Windows `.exe` installer | PARTIAL | builds (`npm run dist:win`, verified Oct 2026, ~111 MB) — **unsigned**, never installed on Windows |
| Server installers | PARTIAL *(were BROKEN)* | Linux `install-server.sh`: missing toolchain fixed, versions pinned — not run on a real Ubuntu/WSL; Windows `install-server.ps1`: missing required inputs and a PowerShell 5.1 incompatibility fixed — never run on Windows |
| One-click install (desktop + server in one installer) | MISSING | the `.exe` installs only the window; the server needs `install-server.ps1` from a repository checkout and the internet once |
| Updates | PARTIAL | documented manual procedure (`bench update` + health check); no auto-update by design |
| Cloud integration (outbox, website orders, catalog/price events) | COMPLETE for the current scope | code (`test_cloud`, `test_sync`, `test_integration`, not re-run). Full two-way sync with the CLOUD ERP is future work |

## Verified in October 2026 (fresh site, Frappe 16.36.1 / ERPNext 16.37.0)

`test_flows` 2, `test_web_pos` 10, `test_pos_shift` 2, `test_pharmacy_core` 15, `test_return_integrity_round4` 19,
`test_localization` 22, `test_operations` 13, `test_api_surface` 1, `test_permissions_matrix` 5,
`test_cost_exposure` 6, new `test_pos_close_shift` 2, new `test_local_journeys` 1 — all pass. Browser run of
`/pos` as a cashier: scan, hold, resume, pay, receipt, close shift with a counted-cash difference.

Not re-run this time: the remaining modules of the 234-test suite (the repository records 234/234 on this
version) and the multi-worker HTTP tools in `dev/`.

## What remains before production

1. Install and run on real Windows 10/11 machines: `install-server.ps1` (WSL2), the `.exe`, reboot and auto-start.
2. A code-signing certificate for the `.exe` (SmartScreen).
3. Real receipt printer and barcode scanner checks; a cash-drawer kick.
4. A single installer (or a guided first-run) that sets up the server without a repository checkout.
5. Voids at the counter (manager approval inside `/pos`), if the pharmacy wants them there.
6. Expired-stock quarantine/disposal, prescriptions/controlled drugs, Iraqi chart of accounts (`PRODUCT.md`).
7. Later: the CLOUD ERP ↔ LOCAL ERP sync engine (not started, by decision).
