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

## Custom app workflow (verified)

```bash
bench new-app pharmacyos_erp                              # scaffold (in its own git repo)
bench --site pharmacyos.localhost install-app pharmacyos_erp
bench --site pharmacyos.localhost migrate
```

The app must live in its **own repository** and be pulled with `bench get-app <url>`. Do not commit
it inside this ERPNext fork.

## Gotchas found in Phase 0

* Run `bench start` from a persistent terminal or session manager. If redis dies mid-way,
  `bench new-site` / `reinstall` fail with `get_system_settings ... NoneType`. Drop and recreate the site.
* Batch tracking is disabled by default in v17. Enable it in Stock Settings with
  `enable_serial_and_batch_no_for_item = 1` before setting `has_batch_no` on any Item.
* The `Batch` DocType is named by random hash in v17. The human lot number is in `batch_id`, and
  transactions link to `Batch.name`. Integrations must resolve `batch_id → name`.
* The IQD currency exists but is **disabled** by default. It ships with 3-decimal / 1000-fils formatting
  (`#,###.###`). Decide the display precision (Iraqi retail prices are whole dinars) before going live.
* `bench get-app payments` (short name) fails, so use the full URL `https://github.com/frappe/payments`.
