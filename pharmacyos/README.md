# PharmacyOS ERP — fork notes

This repository is a fork of [ERPNext](https://github.com/frappe/erpnext) (GPL-3.0). All
PharmacyOS-specific material in this fork lives in this `pharmacyos/` directory, so upstream merges stay
conflict-free. PharmacyOS features are built in a separate Frappe app (`pharmacyos_erp`), not in
ERPNext core.

* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): Phase 0 architecture, gap matrix, integration and UI strategy
* [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md): development stack and bench setup
* [`dev/setup-dev-bench.sh`](dev/setup-dev-bench.sh): reproducible dev bench bootstrap
* [`dev/mariadb-frappe.cnf`](dev/mariadb-frappe.cnf): MariaDB utf8mb4 settings required by Frappe

ERPNext is a trademark of Frappe Technologies Pvt. Ltd. (see `../TRADEMARK_POLICY.md`). PharmacyOS ERP
is built on ERPNext and is not affiliated with or endorsed by Frappe.
