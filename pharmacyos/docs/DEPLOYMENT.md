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

## Stable base: version-16

Production runs on **stable Frappe/ERPNext `version-16`** (upstream), plus the PharmacyOS ERP app.

* The app has no ERPNext core changes, so it installs on upstream stable as is.
* **Commercial target: Frappe 16.36.1 / ERPNext 16.37.0, Python 3.14, MariaDB 10.11.**
* Tested: the full PharmacyOS suite (157 tests) passes on that version-16 bench, on a CI-warmed test site
  and on a brand-new site (erpnext + pharmacyos_erp only), twice in a row, module by module in reverse
  order, and test by test on a pristine database. The develop tree is not a release target.
* The app ships both desk shells:
  * develop: Dock/Sidebar;
  * version-16: Workspace Sidebar + Desktop Icon.
* Routes adapt automatically (`/desk` on develop, `/app` on version-16).

## First-run pharmacy setup — tested

```
bench --site <site> execute pharmacyos_erp.setup.first_run.setup_pharmacy --kwargs '{"pharmacy_name": "Al Noor Pharmacy", "pharmacy_name_ar": "صيدلية النور", "owner_email": "owner@example.com", "owner_password": "…"}'
```

This replaces ERPNext's setup wizard and configures:

* the company: IQD, Iraq, Asia/Baghdad, Arabic;
* the chart of accounts and fiscal year;
* the first branch with its warehouse and storefront code;
* the Branch accounting dimension **with all its columns, created synchronously** — a Purchase
  Receipt and a counter sale work the moment the call returns, with no background worker running;
* PharmacyOS settings (FEFO, batches, backups, website-reservation protection, 12-hour sessions);
* the owner account (Pharmacy Owner roles + System Manager, Arabic).

Guarantees:

* Inputs (name, email, password ≥ 8 characters, branch) are validated before anything is created.
* Re-running after an interruption completes whatever is missing.
* Once the pharmacy is initialised, a further call changes nothing — no password reset, no new
  identity, no role change — and returns `{"status": "already_initialized"}`.
* The health check (`backup.service.health_check`, System Status) fails when a Branch dimension
  column is missing; `bench migrate` repairs missing columns.

`install-server.sh` runs it from `PHARMACY_NAME`, `OWNER_EMAIL` and related variables.

Fresh-install acceptance check (new site, no worker, first run, immediate receipt and sale, second
first run): `DB_ROOT_PASSWORD=… pharmacyos/dev/fresh-site-check.sh <disposable-site>` from the bench
directory. It drops and recreates the site, so never point it at real data.

## Server installation

### Windows PC — the PharmacyOS Setup (owner guide: `INSTALL_WINDOWS.md`)

`PharmacyOS-Setup-<version>.exe` is the only thing the pharmacy runs. It installs the desktop app **and carries
the server bundle** (`resources/server`: the `pharmacyos_erp` app, `deploy/`, `dev/setup-dev-bench.sh`,
`VERSION`), so no repository checkout is needed. On first start the app's setup screens:

1. ask the role of this PC (pharmacy server / connect to a server on the network);
2. ask the pharmacy's name (EN/AR), owner name, email, password, phone, network sharing — validated;
3. run `deploy/windows/system-check.ps1` (Windows build ≥ 19041, 64-bit, memory, free disk, virtualization,
   WSL, an existing server, port 80, administrator) and show each result;
4. start `deploy/windows/install-server.ps1` **elevated, once** (one UAC prompt). The request (with the owner
   password) is written to the user's app-data folder and deleted by the installer after use;
5. follow progress from `%ProgramData%\PharmacyOS\Setup\state.json` (step, percent, message, log tail).

`install-server.ps1` steps: check → WSL (`wsl --install --no-distribution`, Windows-feature fallback) →
**reboot if needed** (state saved; a logon task and RunOnce entry restart the app with `--resume-setup`, which
continues where it stopped) → Ubuntu 24.04 WSL image imported as distro **PharmacyOS** (systemd on) →
`install-server.sh` inside it → **PharmacyOS Server** scheduled task (at startup and at logon, runs
`pharmacyos-server run`, restarted on failure) → optional firewall rule → wait until the server answers.
Passwords never appear in the log. Port 80 is used, or 8780 when 80 is taken; the choice is saved.

**Tested:** Windows CI (windows-latest, Windows PowerShell 5.1): scripts parse, helpers behave, the system
check runs on real Windows; the installer installs silently, opens (first-run screenshot) and uninstalls
keeping data. Electron setup screens (validation, check, progress, reboot, failure, done, resume) driven with
Playwright on Linux. **Not tested:** the WSL2 part on a physical Windows PC (GitHub's Windows runners cannot
run WSL2) — that is production validation.

### Linux server / inside WSL — `deploy/server/install-server.sh` — Tested on a fresh Ubuntu 24.04

Run as root; resumable (each of its 9 steps leaves `/var/lib/pharmacyos/install/<step>.done`, a rerun
continues after the last completed step):

1. packages (MariaDB, Redis, nginx, supervisor, build tools);
2. toolchain — Node.js 24.21.0 from nodejs.org and uv 0.11.32 from GitHub, both SHA-256 verified;
3. database (generated root password, `unix_socket OR mysql_native_password`);
4. app release copied into `/opt/pharmacyos/releases/<version>`, `/opt/pharmacyos/app` → it;
5. bench (Python 3.14.6, bench 5.31.0, **Frappe v16.36.1 / ERPNext v16.37.0**, the app), site;
6. site config (data folder, scheduler on, production mode);
7. first run (`setup_pharmacy`: company, branch, owner, counter **Main Counter** with cash);
8. production: supervisor + nginx configs generated by bench (without `bench setup production`, which would
   pip-install Ansible), install-time Redis stopped, services enabled;
9. finish: `/opt/pharmacyos/bin/pharmacyos-server` installed.

```
sudo PHARMACY_NAME="Al Noor Pharmacy" PHARMACY_NAME_AR="صيدلية النور" OWNER_EMAIL=owner@example.com \
     OWNER_PASSWORD=… ./deploy/server/install-server.sh
```

Secrets (database root and Administrator passwords, generated) are kept in `/etc/pharmacyos/server.env`
(root only). **Tested** in a chroot of the exact Ubuntu 24.04 WSL image the Windows setup imports: install from
nothing, all 7 supervisor programs running, health ok, owner sign-in, Arabic UI, `/pos`; a failure in the
middle resumed correctly.

### Server control — `pharmacyos-server`

```
wsl -d PharmacyOS --user root --exec pharmacyos-server start|stop|status|health|version
wsl -d PharmacyOS --user root --exec pharmacyos-server backup|list-backups|restore <folder> [--with-files]
wsl -d PharmacyOS --user root --exec pharmacyos-server update <bundle-folder>
```

Machine-readable results are printed as `##PHARMACYOS-RESULT {json}`; the desktop app uses them.

## Desktop app (Electron) — `pharmacyos/desktop`

**Why Electron:** it runs the same Chromium that every screen was QA'd in (RTL, fonts, printing). It also
gives control over silent receipt printing, keeps the sign-in session across restarts, and needs no Rust
toolchain.

**Security:**
* context isolation, sandbox, no Node.js in pages;
* navigation locked to the server origin;
* external links open in the system browser;
* the only extra web permissions are notifications, clipboard write and fullscreen;
* only the app's own local screens (setup, startup, backups) get the setup/server/backup bridge.

**Product behaviour:**
* single instance (a second launch focuses the open window);
* startup screen while the server starts (single-PC mode starts it and waits), then the app; if the server
  does not answer, a **recovery** screen (Start the server / Try again / Backups / the log);
* when the installer's server bundle is newer than the installed server, an **Update now / Later** screen;
* menu: Point of Sale, ERP, **Backups…**, **Connection & Printer…**, **Print a test receipt**, About (versions);
* closing is refused while setup, an update or a restore is running;
* the server is pinged every 30 seconds; an Arabic/English banner appears if it drops.

**PharmacyOS POS (`/pos`) — the same screen in the browser and in this app (see `WEB_POS.md`):**
* Settings → *Open at start* → **Point of Sale** makes a counter PC open the POS directly.
* Receipts print through the app's native bridge: silently to the chosen receipt printer (with *Print
  receipts directly*), otherwise with the system dialog. The bridge accepts only the server's own
  `/printview`, from the server's own pages.

**Barcode scanners:** keyboard (HID) devices; Enter or Tab suffix; nothing is installed for them.

**Installer:** NSIS, per-machine, desktop and Start-menu shortcuts, opens the app when finished; data folders in
`%ProgramData%\PharmacyOS` (kept on uninstall). The uninstaller asks (default **No**) whether to remove the
pharmacy server too; on Yes `uninstall-server.ps1` takes a final backup first and keeps the Backups folder.
An upgrade never removes the server. Signing: `RELEASE.md`.

**Tested here:** `npm test` (12); Playwright-driven Electron on Linux + Xvfb: every setup screen, startup to the
sign-in, recovery → start the server, update offer → updated, backups list / back up now / restore with
typed confirmation; Windows CI install/launch/uninstall. **Hardware-only:** real Windows 10/11 + WSL2,
reboot/auto-start, scanners, thermal printers, SmartScreen with a signed installer.

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

This was tested onto a fresh site and in place, including with an encrypted backup. On the pharmacy PC the
owner restores from **PharmacyOS → Backups…** (typed confirmation; runs `pharmacyos-server restore`, which takes
the safety backup first) — tested end-to-end on the fresh-install server.

## Updates — guided, with rollback

* **Desktop**: install the newer Setup over the old one; data and settings are kept.
* **Server**: at the next start the app offers **Update now / Later** when the bundled server is newer.
  `pharmacyos-server update` takes a verified safety backup, turns maintenance mode on, installs the new release
  next to the old one, runs `bench migrate` and `bench build`, restarts and checks health. **Any failure rolls
  back**: previous release, safety backup restored, services restarted — tested with a real failed update and
  with five successful updates (rc1 → rc6) on the fresh-install server.
* Nothing updates unattended; there is no update feed (`RELEASE.md`).

## Disaster recovery

| Event | Behaviour |
|---|---|
| Windows reboot or power cut | Services start at boot (scheduled task "PharmacyOS Server", systemd, supervisor). MariaDB recovers InnoDB. An interrupted backup is reconciled as Failed and the next hour runs normally. |
| Desktop app crash | Reopen it. The session persists, and nothing unsaved lives in the app. |
| Server unreachable | Offline screen with Retry. The banner warns not to re-enter a sale. |
| Internet outage | Sales continue. Cloud events queue with back-off; Retry is on System Status. |
| Failed backup | Logged, alert on dashboard and System Status, previous backups kept, retried next hour. |
| Disk nearly full | Backup skipped with a clear error before writing. Retention frees space. |
