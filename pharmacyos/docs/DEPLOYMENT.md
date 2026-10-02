# PharmacyOS ERP — deployment, backup, recovery and desktop

Status labels: **Tested** means it ran here and was checked. **Untested** means written, but not run on its
target (Windows). **Planned** means not built yet.

## Architecture

```
Pharmacy server (Linux or the PharmacyOS WSL2 environment on Windows)    ← one authoritative database
  ├── nginx → gunicorn (PharmacyOS ERP)        supervisor: workers, scheduler, socket.io
  ├── MariaDB  (pharmacy database)             Redis (cache / queues)
  ├── hourly backup + sales spreadsheets       → PharmacyOS data folder
  └── outbox → PharmacyOS Cloud (when configured; queued while offline)
        ▲  pharmacy LAN (http://<server>/)
  PharmacyOS ERP desktop app on each PC (POS, office, pharmacist) — a window onto the server;
  stores no pharmacy data and no passwords.
```

* **Single-PC pharmacy:** the same Windows PC runs the server environment (WSL2) and the desktop app.
* **Several PCs:** one PC or a mini-server is the server. The other PCs run only the desktop app and choose
  "Connect to the pharmacy server". There is never a second, competing pharmacy database.
* **Internet loss** does not stop sales. POS, search, stock and accounting run on the local server. Cloud
  events wait in the outbox and are delivered once the connection returns; delivery is idempotent by
  event ID (Flow F is tested).

## Server installation

### Linux server — Untested end-to-end

```
sudo SITE=pharmacy.local ADMIN_PASSWORD=… DB_ROOT_PASSWORD=… ./deploy/server/install-server.sh
```

* Installs MariaDB, Redis, nginx and supervisor.
* Creates the bench, apps and site using the same steps as the dev bootstrap, which is tested.
* Then runs `bench setup production`, which makes the services start at boot.

### Single Windows PC — Untested

```
deploy/windows/install-server.ps1 -Site pharmacy.local [-ShareOnNetwork]
```

Run it as Administrator. It:

* imports an Ubuntu 24.04 WSL2 distro named **PharmacyOS**;
* enables systemd and keeps the VM alive;
* runs `install-server.sh` inside the distro;
* registers the **PharmacyOS Server** scheduled task, which starts at boot before anyone signs in;
* with `-ShareOnNetwork`, turns on mirrored networking and opens a firewall rule on private networks.

Control the server with:

```
wsl -d PharmacyOS --exec /opt/pharmacyos/bin/pharmacyos-server start|stop|status|health
```

## Desktop app (Electron) — `pharmacyos/desktop`

**Why Electron:** it runs the same Chromium that every screen was QA'd in (RTL, fonts, printing). It also
gives control over silent receipt printing, keeps the sign-in session across restarts, and needs no Rust
toolchain.

**Security:**
* context isolation, sandbox, no Node.js in pages;
* navigation locked to the server origin;
* external links open in the system browser;
* the only extra web permissions are notifications, clipboard write and fullscreen.

**First run:** "This computer is the pharmacy server" or "Connect to the pharmacy server" (enter its
address). You can also pick a receipt printer and choose to print receipts without a dialog.

**At runtime:**
* the server is pinged every 30 seconds;
* if the connection drops, an Arabic or English banner appears without reloading the page;
* in single-PC mode, the app starts the server if it is down.

**Barcode scanners:** they work as keyboard (HID) devices; nothing is installed for them.

**Tested here (Linux + Xvfb):**
* first-run setup screen;
* connecting to a live server opens the Arabic sign-in;
* an unreachable server shows the offline screen;
* config unit tests: `npm test`.

**Windows installer:**
* The installer is defined: NSIS, per-machine, desktop and Start-menu shortcuts.
* Data folders go in `%ProgramData%\PharmacyOS`. Uninstall never deletes them.
* Build it with the **PharmacyOS Desktop (Windows)** GitHub workflow (manual or on a tag) or `npm run dist:win`
  on Windows.
* It could not be produced in this Linux container: NSIS needs 32-bit Wine.

**Not built yet (Planned):**
* code signing (needs a certificate purchase);
* auto-update (deliberately not enabled, see Updates);
* a cash-drawer kick.

## Backups — Tested

| | |
|---|---|
| Schedule | Database backup every hour. Once a day: database + files, plus day reports. Scheduler jobs `hourly_long` / `daily_long`. |
| Method | Frappe's dump (`mariadb-dump --single-transaction`, gzip): a consistent snapshot while selling continues. Live database files are never copied. |
| Location | `<data>/Backups/<YYYY-MM-DD>/<HH-MM>/` holding `database.sql.gz` (+ `files.tar`, `private-files.tar` daily) and `metadata.json` (site, pharmacy, versions, time zone, SHA-256 per file). `<data>` = site config `pharmacyos_data_dir`, else PharmacyOS Settings → Data Folder, else `<bench>/pharmacyos-data/<site>`. On a single Windows PC: `C:\ProgramData\PharmacyOS`. |
| Spreadsheets | `<data>/Sales/<date>/Sales-HH-00.xlsx` hourly, with every sale/return line of the day: invoice, time, cashier, branch, customer, barcode, medicine (EN/AR), batch, expiry, qty, price, discount, totals, payment method, status. `<data>/Daily Reports/<date>/`: Daily-Sales, Payment-Summary, Inventory-Summary .xlsx and Backup-Metadata.json. |
| Verification | Each backup is checksummed and test-decompressed, and is only marked *Success* once verified. |
| Encryption | Turn on System Settings → **Encrypt Backups** (gpg, AES). The passphrase is generated into the server's `site_config.json` and is never in source code. **Store a copy offline: without it an encrypted backup cannot be restored on a new machine.** |
| Retention | PharmacyOS Settings: every backup is kept for *Keep Hourly Backups (Days)* (default 2). The last backup of each day is kept for *Keep Daily Backups (Days)* (default 30). The newest verified backup is never deleted. Spreadsheets follow the daily retention. |
| Health | **System → System Status**: last success, next run, size, folder, free disk, last error. A dashboard alert appears only on a problem. A backup is skipped with a warning below *Minimum Free Disk*. Failures are logged and never interrupt a sale. |
| Audit | *PharmacyOS Backup Log* is read-only in the UI. Rows left "Running" by a power cut or restore are reconciled against the files on disk. |
| Access | Back up now: Owner / System Manager. Status: Owner, Manager, System Manager. Cashiers have no access (tested). |

### Restore — Tested on Linux

```
DB_ROOT_PASSWORD=… ./deploy/restore-backup.sh <site> <backup-folder> [--with-files] [--yes]
```

It runs these steps:

1. verify checksums;
2. ask the operator to confirm by typing the site name;
3. take a **safety backup** of the current state;
4. turn maintenance mode on;
5. `bench restore`, which decrypts automatically when the site's key is present;
6. `migrate`;
7. turn maintenance mode off;
8. health check;
9. write an audit entry;
10. then restart the services.

This was tested onto a fresh site and in place, including with an encrypted backup. Restore is a server
operation, never a button in the UI.

## Updates — procedure, not automatic

1. Back up now (System Status) and confirm it succeeded.
2. `bench update --pull --patch --build`, or `git pull` + `bench migrate` + `bench build`.
3. `pharmacyos-server health`.
4. If migration fails, restore the pre-update backup with `restore-backup.sh`.

The desktop app shows its version. Silent auto-update is deliberately not enabled, so a database is never
upgraded unattended.

## Disaster recovery

| Event | Behaviour |
|---|---|
| Windows reboot or power cut | Services start at boot (scheduled task, systemd). MariaDB recovers InnoDB. An interrupted backup is reconciled as Failed and the next hour runs normally. |
| Desktop app crash | Reopen it. The session persists, and nothing unsaved lives in the app. |
| Server unreachable | Offline screen with Retry. The banner warns not to re-enter a sale. |
| Internet outage | Sales continue. Cloud events queue with back-off; Retry is on System Status. |
| Failed backup | Logged, alert on dashboard and System Status, previous backups kept, retried next hour. |
| Disk nearly full | Backup skipped with a clear error before writing. Retention frees space. |
