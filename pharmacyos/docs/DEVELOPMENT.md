# PharmacyOS ERP — Development Environment

Development only. Never reuse these credentials, sites or settings in production, and never point a
dev bench at production PharmacyOS data.

## Stack (verified in Phase 0)

| Component | Required | Source of truth |
|---|---|---|
| ERPNext (this fork) | `17.0.0-dev` on `develop` | `erpnext/__init__.py` |
| Frappe Framework | `>=17.0.0-dev,<18` (`develop`) | `pyproject.toml` → `[tool.bench.frappe-dependencies]` |
| Python | `>=3.14,<3.15` (tested 3.14.8) | `pyproject.toml`, Frappe `pyproject.toml` |
| Node.js | `>=24` (tested 24.21.0) + yarn 1.x | Frappe `package.json` engines; CI image `py3.14-node24` |
| Database | MariaDB (tested 10.11), utf8mb4 / `utf8mb4_unicode_ci` | `.github/helper/install.sh` |
| Redis | 2 instances: cache (:13000) and queue (:11000), started by `bench start` | bench `config/` |
| Bench CLI | `frappe-bench` (tested 5.31.0), installed via `uv tool install` | |
| Test-only app | `frappe/payments` (`develop`); CI installs it on the test site | `.github/helper/site_config_mariadb.json` |

PostgreSQL is also supported by upstream CI, but MariaDB is the primary target. Use MariaDB.

`version-16` (stable, latest tag v16.37.0) needs the same Python 3.14, Node 24 and MariaDB stack. See
"Branch choice" in `ARCHITECTURE.md`.

## Bootstrap

Run as a **non-root** user, because bench refuses to run as root:

```bash
sudo apt-get install -y git mariadb-server mariadb-client libmariadb-dev pkg-config redis-server cron xvfb libfontconfig1
sudo cp pharmacyos/dev/mariadb-frappe.cnf /etc/mysql/mariadb.conf.d/99-frappe.cnf && sudo systemctl restart mariadb
# set a local MariaDB root password (mysql_native_password), then:
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv → Python 3.14 + bench
nvm install 24 && corepack enable yarn                   # Node 24 + yarn 1.x

DB_ROOT_PASSWORD=... ERPNEXT_REPO=https://github.com/44mmd/PharmacyOS-ERP ERPNEXT_BRANCH=develop \
  ./pharmacyos/dev/setup-dev-bench.sh
```

The script creates `~/frappe-bench` with these sites:

* `pharmacyos.localhost`: the development site (developer_mode on). Finish the setup wizard there
  with country **Iraq**, currency **IQD** and timezone **Asia/Baghdad**, using a fictitious company.
* `test.localhost`: the automated-test site (`allow_tests`). It has erpnext + payments installed and
  fixtures warmed the same way upstream CI does. Tests create and delete records, so never run them
  on the dev site.

Daily commands:

```bash
cd ~/frappe-bench
bench start                                   # web :8000, socketio :9000, workers, scheduler, redis, watch
bench --site pharmacyos.localhost migrate     # after pulling ERPNext / app changes
bench build [--app erpnext]                   # rebuild assets
bench --site test.localhost run-tests --lightmode --module erpnext.stock.doctype.batch.test_batch
bench --site pharmacyos.localhost console     # IPython with frappe loaded
```

On Docker: the official containerised dev workflow is `frappe/frappe_docker` (devcontainer). It runs
the same bench commands inside containers. Use it if a native install is impractical, but keep this
bench layout and do not treat the `pwd.yml` demo compose file as a dev environment.

## The `pharmacyos_erp` app

All PharmacyOS functionality is in the custom app at `pharmacyos/apps/pharmacyos_erp` (self-contained,
movable to `44mmd/pharmacyos_erp` with `git subtree split`, see its README). `setup-dev-bench.sh`
links it into the bench and installs it on both sites. Manually:

```bash
ln -sfn /path/to/PharmacyOS-ERP/pharmacyos/apps/pharmacyos_erp ~/frappe-bench/apps/pharmacyos_erp
cd ~/frappe-bench && uv pip install -e "apps/pharmacyos_erp[test]" --python env/bin/python   # [test]: freezegun
echo pharmacyos_erp >> sites/apps.txt            # make sure the file ends with a newline first
bench --site pharmacyos.localhost install-app pharmacyos_erp
bench --site test.localhost install-app pharmacyos_erp
bench build --app pharmacyos_erp
```

Development data (fictitious medicines, two branches, receipts with batches, sales, an open purchase
order — all real ERPNext documents; developer mode only):

```bash
bench --site pharmacyos.localhost execute pharmacyos_erp.setup.demo_data.create_demo_data
```

Tests (on the test site only):

```bash
bench --site test.localhost run-tests --app pharmacyos_erp                     # PharmacyOS (268 tests)
bench --site test.localhost run-tests --lightmode --module erpnext.stock.doctype.batch.test_batch   # upstream
```

The PharmacyOS suite is self-contained: it creates ERPNext's standard test masters itself
(`tests/utils.py → ensure_test_masters`, ERPNext's own idempotent BootStrapTestData), so it passes on a
brand-new site with only erpnext + pharmacyos_erp installed — no `payments` app, no CI warm-up run — and
gives the same result whatever runs first: each test alone, each module alone, modules in any order, and
the full suite repeatedly on the same database.

Acceptance tools against a running server (several gunicorn workers; never a production site):

```bash
# simultaneous returns, last-unit sales and duplicate checkouts, released at the same instant
python3 pharmacyos/dev/concurrency_check.py --site <site> --key K --secret S --setup-key AK --setup-secret AS \
    --integration-key IK --integration-secret IS --rounds 8
# the staff / API permission matrix (one API key per role profile, see the script)
python3 pharmacyos/dev/permission_check.py --site <site> --actors actors.json
# cost-data red-team: no purchase price / valuation / stock value for counter staff
python3 pharmacyos/dev/permission_check.py --site <site> --actors actors.json --cost
# the round-4 sentinel matrix: four distinct cost sentinels, 71 surfaces, every profile and Guest
python3 pharmacyos/dev/cost_matrix_check.py --site <site> --actors actors.json
# a real Cloud next to the ERP: catalog, public price validity, outage, orders, completion
python3 pharmacyos/dev/live_cloud_check.py --site <site> --actors actors.json \
    --cloud-dir /path/to/PharmacyOS/backend --cloud-python /path/to/venv/bin/python
```

`concurrency_check.py` also races returns against the cancellation of their original sale
(`original_cancel`: simultaneous, staggered both ways, several returns, return cancellation, and a
repeat of the losing operation) and sales against the cancellation of unrelated documents
(`unrelated_cancel`, round 4: no database deadlock, every request answered 200 or with a business error). The test site should allow as many test users as upstream CI does
(`bench --site test.localhost set-config --parse throttle_user_limit 100`).

Navigation (Dock + Sidebars) is generated from `pharmacyos_erp/setup/navigation.py`; after changing it,
bump `MODIFIED` there and regenerate (`bench --site <site> execute pharmacyos_erp.setup.navigation.write_files`
in developer mode), then `bench migrate`.

## Gotchas

* Run `bench start` from a persistent terminal or session manager. If redis dies mid-way,
  `bench new-site` / `reinstall` fail with `get_system_settings ... NoneType`. Drop and recreate the site.
* Batch tracking is disabled by default in v17. Enable it in Stock Settings with
  `enable_serial_and_batch_no_for_item = 1` before setting `has_batch_no` on any Item.
* The `Batch` DocType is named by random hash in v17. The human lot number is in `batch_id`, and
  transactions link to `Batch.name`. Integrations must resolve `batch_id → name`.
* The IQD currency exists but is **disabled** by default. It ships with 3-decimal / 1000-fils formatting
  (`#,###.###`). Decide the display precision (Iraqi retail prices are whole dinars) before going live.
* `bench get-app payments` (short name) fails, so use the full URL `https://github.com/frappe/payments`.
* `bench migrate` only re-imports app JSON (sidebars, pages, print formats) whose `modified` is newer
  than the database copy — bump it when you change them.
* The `watch` process in the Procfile rebuilds assets on file changes and races manual `bench build`
  runs (stale or missing RTL CSS). This dev bench comments it out; run `bench build --app pharmacyos_erp`
  explicitly. Killing one Procfile process stops all of them (honcho), so restart `bench start` after.
* Frappe v17 rejects SQL functions as strings in `frappe.get_all(fields=...)`; use
  `{"SUM": "field", "as": "alias"}`.
* ERPNext v17 requires **Accounts User** to create Sales/POS Invoices and **Sales Manager** for POS
  opening/closing entries; the PharmacyOS role profiles account for the first, and leave shift
  opening/closing to managers (see ARCHITECTURE.md).
* Frappe's login context reads `frappe.local.request`; tests that render `/login` must call
  `frappe.utils.set_request()` first.
