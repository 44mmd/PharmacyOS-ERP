# PharmacyOS ERP — fork notes

This repository is a fork of [ERPNext](https://github.com/frappe/erpnext) (GPL-3.0). All
PharmacyOS-specific material in this fork lives in this `pharmacyos/` directory, so upstream merges stay
conflict-free. PharmacyOS features are built in a separate Frappe app (`pharmacyos_erp`), not in
ERPNext core.

* [`apps/pharmacyos_erp`](apps/pharmacyos_erp): **the PharmacyOS ERP Frappe app** (all product code)
* [`docs/LOCAL_ERP_STATUS.md`](docs/LOCAL_ERP_STATUS.md): what the LOCAL ERP does today — every subsystem classified (Oct 2026), and what remains
* [`docs/PRODUCT.md`](docs/PRODUCT.md): product transformation — identity, navigation, roles, workflows, remaining gaps
* [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md): server and Windows desktop deployment, hourly backups, restore, updates, disaster recovery
* [`docs/WEB_POS.md`](docs/WEB_POS.md): the PharmacyOS POS (`/pos`) — one POS for the web browser and the Windows desktop app; architecture, security, tests, deployment, manual test
* [`desktop/`](desktop): PharmacyOS ERP Windows desktop app (Electron)
* [`deploy/`](deploy): server install, Windows single-PC setup, backup restore tool
* [`docs/LOCALIZATION.md`](docs/LOCALIZATION.md): Arabic-first localization — terminology (Batch = الوجبة), RTL/bidi, IQD, dates, Arabic search
* [`docs/INTEGRATION_API.md`](docs/INTEGRATION_API.md): PharmacyOS ↔ ERP API v1 and outbound events
* [`FORK_PATCHES.md`](FORK_PATCHES.md): ERPNext core changes (currently none)
* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): Phase 0 architecture, gap matrix, integration and UI strategy
* [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md): development stack and bench setup
* [`dev/setup-dev-bench.sh`](dev/setup-dev-bench.sh): reproducible dev bench bootstrap
* [`dev/mariadb-frappe.cnf`](dev/mariadb-frappe.cnf): MariaDB utf8mb4 settings required by Frappe

ERPNext is a trademark of Frappe Technologies Pvt. Ltd. (see `../TRADEMARK_POLICY.md`). PharmacyOS ERP
is built on ERPNext and is not affiliated with or endorsed by Frappe.
