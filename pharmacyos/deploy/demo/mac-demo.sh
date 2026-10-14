#!/usr/bin/env bash
# PharmacyOS Local ERP — one-command DEMO on Ubuntu 24.04 (ARM64 in Parallels on an Apple-silicon Mac, or x86_64).
#
#   sudo bash pharmacyos/deploy/demo/mac-demo.sh
#
# Installs OUR customized ERP (Frappe 16.36.1 / ERPNext 16.37.0 + the pharmacyos_erp app) with the normal
# server installer, then loads fictitious demo data (two demo branches, medicines with Arabic names, batches,
# suppliers, purchases, sales, two POS counters with a demo cashier and manager). Nothing connects to the
# Cloud: the Local <-> Cloud integration stays off. For a demo machine only — never a real pharmacy.
# Safe to run again: the installer resumes where it stopped and the demo data is created only once.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
INSTALLER="$HERE/../server/install-server.sh"

[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo:  sudo bash $0" >&2; exit 1; }
if ! grep -q 'VERSION_ID="24.04"' /etc/os-release 2>/dev/null; then
	echo "Warning: PharmacyOS is tested on Ubuntu 24.04; this machine runs: $(. /etc/os-release; echo "$PRETTY_NAME")" >&2
fi

export PHARMACY_NAME="${PHARMACY_NAME:-PharmacyOS Demo Pharmacy}"
export PHARMACY_NAME_AR="${PHARMACY_NAME_AR:-صيدلية PharmacyOS التجريبية}"
export OWNER_FULL_NAME="${OWNER_FULL_NAME:-Demo Owner}"
export OWNER_EMAIL="${OWNER_EMAIL:-owner@demo.invalid}"
export OWNER_PASSWORD="${OWNER_PASSWORD:-DemoOwner-2026}"
DEMO_CASHIER_PASSWORD="${DEMO_CASHIER_PASSWORD:-DemoCashier-2026}"
DEMO_MANAGER_PASSWORD="${DEMO_MANAGER_PASSWORD:-DemoManager-2026}"

echo "== 1/2  Installing the PharmacyOS server (about 20–40 minutes the first time) ..."
bash "$INSTALLER"

# shellcheck disable=SC1091
. /etc/pharmacyos/server.env
BENCH_USER="${BENCH_USER:-frappe}"
as_bench() { su - "$BENCH_USER" -c "export PATH=\"\$HOME/.local/bin:/usr/local/bin:\$PATH\"; cd '$BENCH_DIR' && $1"; }

echo "== 2/2  Loading the demo data (fictitious) ..."
MARK=/var/lib/pharmacyos/install/demo_data.done
if [ ! -f "$MARK" ]; then
	# the demo loaders refuse to run unless developer mode is on: switch it on only while they run
	as_bench "bench --site '$SITE' set-config developer_mode 1"
	trap 'as_bench "bench --site \"$SITE\" set-config developer_mode 0" || true' EXIT
	as_bench "bench --site '$SITE' execute pharmacyos_erp.setup.demo_data.create_demo_data"
	kw="$(python3 -c 'import json,sys;print(json.dumps({"cashier_password":sys.argv[1],"manager_password":sys.argv[2]}))' "$DEMO_CASHIER_PASSWORD" "$DEMO_MANAGER_PASSWORD")"
	as_bench "bench --site '$SITE' execute pharmacyos_erp.setup.pos_demo.create_pos_demo --kwargs '$kw'"
	as_bench "bench --site '$SITE' set-config developer_mode 0 && bench --site '$SITE' clear-cache"
	trap - EXIT
	date -Is > "$MARK"
else
	echo "   (demo data already loaded)"
fi
/opt/pharmacyos/bin/pharmacyos-server start >/dev/null 2>&1 || true

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

=====================================================================
 PharmacyOS demo is ready.  Open in a browser (on the Mac too):

   http://${IP:-<this VM's IP>}/            the ERP desk
   http://${IP:-<this VM's IP>}/pos         the counter (Point of Sale)

 Sign in as:
   Owner     ${OWNER_EMAIL}          ${OWNER_PASSWORD}
   Manager   manager@pos-test.invalid    ${DEMO_MANAGER_PASSWORD}
   Cashier   cashier@pos-test.invalid    ${DEMO_CASHIER_PASSWORD}

 Stop / start:   sudo /opt/pharmacyos/bin/pharmacyos-server stop | start
 Demo data only — no Cloud connection, not for a real pharmacy.
=====================================================================
EOF
