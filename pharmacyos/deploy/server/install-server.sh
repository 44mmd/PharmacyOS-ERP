#!/usr/bin/env bash
# PharmacyOS server installation (Ubuntu 24.04: a pharmacy's Linux server, or the PharmacyOS WSL2
# environment on a single Windows PC). Production layout: nginx + supervisor (gunicorn, workers,
# scheduler, socket.io), MariaDB, Redis — all started at boot.
#
#   sudo SITE=pharmacy.local ADMIN_PASSWORD=... DB_ROOT_PASSWORD=... \
#        PHARMACY_NAME="Al Noor Pharmacy" PHARMACY_NAME_AR="صيدلية النور" \
#        OWNER_EMAIL=owner@example.com OWNER_PASSWORD=... \
#        PHARMACYOS_DATA_DIR=/mnt/c/ProgramData/PharmacyOS ./install-server.sh
#
# Base: stable Frappe 16.36.1 / ERPNext 16.37.0 (upstream tags) + the PharmacyOS ERP app from this repository.
# PharmacyOS needs no ERPNext core changes (see FORK_PATCHES.md), so upstream stable is used as is.
#
# PHARMACYOS_DATA_DIR: backups and sales spreadsheets. On a single Windows PC point it at
# /mnt/c/ProgramData/PharmacyOS so they live on the Windows disk, outside the Linux environment.
set -euo pipefail
: "${SITE:?SITE (e.g. pharmacy.local)}" "${ADMIN_PASSWORD:?ADMIN_PASSWORD}" "${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD}"
: "${PHARMACY_NAME:?PHARMACY_NAME}" "${OWNER_EMAIL:?OWNER_EMAIL}" "${OWNER_PASSWORD:?OWNER_PASSWORD}"
DATA_DIR="${PHARMACYOS_DATA_DIR:-/var/lib/pharmacyos}"
BENCH_USER="${BENCH_USER:-frappe}"
ERPNEXT_REPO="${ERPNEXT_REPO:-https://github.com/frappe/erpnext}"
# Pinned to the release the PharmacyOS suite is verified on (DEPLOYMENT.md, "commercial target"). The moving
# version-16 branch head is not used: v16.50.0 created sites without the Gender DocType (Oct 2026).
FRAPPE_BRANCH="${FRAPPE_BRANCH:-v16.36.1}"
ERPNEXT_BRANCH="${ERPNEXT_BRANCH:-v16.37.0}"
HERE="$(cd "$(dirname "$0")" && pwd)"

apt-get update
apt-get install -y git curl mariadb-server mariadb-client libmariadb-dev pkg-config redis-server \
	nginx supervisor cron gpg xvfb libfontconfig1
install -m 0644 "$HERE/../../dev/mariadb-frappe.cnf" /etc/mysql/mariadb.conf.d/99-frappe.cnf
systemctl enable --now mariadb redis-server
mariadb -uroot -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '${DB_ROOT_PASSWORD}'; FLUSH PRIVILEGES;" || true

id "$BENCH_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$BENCH_USER"
mkdir -p "$DATA_DIR" && chown "$BENCH_USER" "$DATA_DIR" || true

# Toolchain the bench needs (setup-dev-bench.sh checks for it): Node.js 24 + yarn 1.x system-wide, and
# uv (Python 3.14 + the bench CLI) for the bench user. Installing needs the internet once; running the
# pharmacy afterwards does not.
NODE_MAJOR=24
if ! node --version 2>/dev/null | grep -q "^v${NODE_MAJOR}\."; then
	install -d -m 0755 /etc/apt/keyrings
	curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
	echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list
	apt-get update
	apt-get install -y nodejs
fi
command -v yarn >/dev/null || npm install -g yarn@1
su - "$BENCH_USER" -c 'command -v uv >/dev/null || [ -x ~/.local/bin/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh'
BENCH_BIN="/home/$BENCH_USER/.local/bin"  # uv, and the bench CLI installed by `uv tool install`

# bench + apps + site (same steps as the development bootstrap, without the test site)
su - "$BENCH_USER" -c "PATH='$BENCH_BIN':\$PATH FRAPPE_BRANCH='$FRAPPE_BRANCH' ERPNEXT_REPO='$ERPNEXT_REPO' ERPNEXT_BRANCH='$ERPNEXT_BRANCH' DB_ROOT_PASSWORD='$DB_ROOT_PASSWORD' \
	ADMIN_PASSWORD='$ADMIN_PASSWORD' DEV_SITE='$SITE' SKIP_TEST_SITE=1 bash '$HERE/../../dev/setup-dev-bench.sh'"
su - "$BENCH_USER" -c "PATH='$BENCH_BIN':\$PATH; cd frappe-bench && bench --site '$SITE' set-config pharmacyos_data_dir '$DATA_DIR' \
	&& bench --site '$SITE' set-config developer_mode 0 && bench --site '$SITE' enable-scheduler \
	&& bench --site '$SITE' set-maintenance-mode off"

# PharmacyOS first-run setup (company, IQD, Iraq, Arabic, first branch, owner) — no ERPNext wizard
python3 - "$SITE" > /tmp/pharmacyos-setup.json <<PY
import json, os, sys
print(json.dumps({k: os.environ.get(v) for k, v in {
	"pharmacy_name": "PHARMACY_NAME", "pharmacy_name_ar": "PHARMACY_NAME_AR", "owner_email": "OWNER_EMAIL",
	"owner_password": "OWNER_PASSWORD", "owner_full_name": "OWNER_FULL_NAME", "phone": "PHARMACY_PHONE",
	"address": "PHARMACY_ADDRESS"}.items() if os.environ.get(v)}))
PY
chown "$BENCH_USER" /tmp/pharmacyos-setup.json && chmod 600 /tmp/pharmacyos-setup.json
su - "$BENCH_USER" -c "PATH='$BENCH_BIN':\$PATH; cd frappe-bench && bench --site '$SITE' execute pharmacyos_erp.setup.first_run.setup_pharmacy --kwargs \"\$(cat /tmp/pharmacyos-setup.json)\""
rm -f /tmp/pharmacyos-setup.json

# production processes (nginx + supervisor), started at boot
cd "/home/$BENCH_USER/frappe-bench"
"$BENCH_BIN/bench" setup production "$BENCH_USER" --yes
systemctl enable nginx supervisor mariadb redis-server
install -m 0755 "$HERE/pharmacyos-server" /opt/pharmacyos/bin/pharmacyos-server 2>/dev/null || {
	mkdir -p /opt/pharmacyos/bin && install -m 0755 "$HERE/pharmacyos-server" /opt/pharmacyos/bin/pharmacyos-server; }
/opt/pharmacyos/bin/pharmacyos-server health
echo "PharmacyOS server ready: http://$(hostname -I | awk '{print $1}')/  (site $SITE)"
echo "IMPORTANT: copy backup_encryption_key from sites/$SITE/site_config.json to an offline, safe place."
