#!/usr/bin/env bash
# PharmacyOS POS — one-command TEST server (fictitious demo pharmacy) on a fresh Ubuntu 24.04 VPS.
#
#   git clone -b claude/pharmacyos-dual-pos-auscie https://github.com/44mmd/PharmacyOS-ERP
#   sudo POS_HOST=pos.halfpharmacy.com CERTBOT_EMAIL=you@example.com PharmacyOS-ERP/pharmacyos/deploy/pos/install-pos-test-server.sh
#
# Before running: create a DNS A record POS_HOST → this server's public IP, and open ports 80 + 443.
#
# It installs the PharmacyOS ERP server (deploy/server/install-server.sh: stable Frappe/ERPNext
# version-16 + PharmacyOS ERP), fills it with the fictitious demo pharmacy (medicines, batches with
# expiry, two branches), creates two POS counters and test accounts with generated passwords, and serves
# the POS at https://POS_HOST/pos. At the end it prints the URL and the test accounts (also saved, root
# only, in /root/pharmacyos-pos-test-credentials.txt).
#
# TEST DATA ONLY. Never import real patients, prices or stock into this server, and never point it at
# the production storefront or admin panel — it is a separate, isolated ERP.
set -euo pipefail
POS_HOST="${POS_HOST:?set POS_HOST, e.g. pos.halfpharmacy.com}"
SITE="${SITE:-pos-test.local}"
HERE="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }

gen() { python3 -c "import secrets; print('$1-' + secrets.token_urlsafe(9))"; }
export SITE ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(gen Admin)}" DB_ROOT_PASSWORD="${DB_ROOT_PASSWORD:-$(gen Db)}"
export PHARMACY_NAME="${PHARMACY_NAME:-PharmacyOS Test Pharmacy}" PHARMACY_NAME_AR="${PHARMACY_NAME_AR:-صيدلية PharmacyOS التجريبية}"
export OWNER_EMAIL="${OWNER_EMAIL:-owner@pos-test.invalid}" OWNER_PASSWORD="${OWNER_PASSWORD:-$(gen Owner)}"
CASHIER_PASSWORD="${CASHIER_PASSWORD:-$(gen Cashier)}"
MANAGER_PASSWORD="${MANAGER_PASSWORD:-$(gen Manager)}"
CREDS=/root/pharmacyos-pos-test-credentials.txt
umask 077
cat > "$CREDS" <<EOF
PharmacyOS POS test server — $(date -u +%F)
URL:        https://$POS_HOST/pos
Cashier:    cashier@pos-test.invalid   $CASHIER_PASSWORD   (Counter 1 — no discounts)
Manager:    manager@pos-test.invalid   $MANAGER_PASSWORD   (Counter 2 — discounts allowed)
Owner:      $OWNER_EMAIL   $OWNER_PASSWORD   (ERP desk: https://$POS_HOST/desk)
Admin:      Administrator   $ADMIN_PASSWORD
MariaDB root: $DB_ROOT_PASSWORD
EOF

# 1. the ERP server (existing installer)
"$HERE/../server/install-server.sh"

# 2. demo pharmacy + POS counters and test accounts (developer mode only while creating demo data)
BENCH="/home/${BENCH_USER:-frappe}/frappe-bench"
as_bench() { su - "${BENCH_USER:-frappe}" -c "cd '$BENCH' && $1"; }
as_bench "bench --site '$SITE' set-config developer_mode 1"
as_bench "bench --site '$SITE' execute pharmacyos_erp.setup.demo_data.create_demo_data"
printf '{"cashier_password": "%s", "manager_password": "%s"}' "$CASHIER_PASSWORD" "$MANAGER_PASSWORD" > /tmp/pos-demo.json
chown "${BENCH_USER:-frappe}" /tmp/pos-demo.json
as_bench "bench --site '$SITE' execute pharmacyos_erp.setup.pos_demo.create_pos_demo --kwargs \"\$(cat /tmp/pos-demo.json)\""
rm -f /tmp/pos-demo.json
as_bench "bench --site '$SITE' set-config developer_mode 0 && bench --site '$SITE' clear-cache"

# 3. the POS hostname with HTTPS
POS_HOST="$POS_HOST" SITE="$SITE" BENCH_DIR="$BENCH" "$HERE/setup-pos-host.sh"
supervisorctl restart all >/dev/null 2>&1 || true

echo
echo "================ PharmacyOS POS test server ready ================"
cat "$CREDS"
echo "(saved in $CREDS — root only)"
echo "Demo barcodes: see pharmacyos/docs/WEB_POS.md (Paracetamol 2000000000015, Ibuprofen 2000000000039, …)"
