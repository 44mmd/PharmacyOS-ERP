#!/usr/bin/env bash
# PharmacyOS ERP — local development bench bootstrap (Phase 0).
#
# Creates a Frappe bench (Frappe `develop`, v17-dev) with this ERPNext fork installed,
# a development site, and a separate test site. Development only: never use these
# credentials or this configuration in production.
#
# Prerequisites (Ubuntu 24.04 tested; run as a NON-root user — bench refuses root):
#   sudo apt-get install -y git mariadb-server mariadb-client libmariadb-dev pkg-config \
#        redis-server cron xvfb libfontconfig1
#   uv           https://docs.astral.sh/uv/   (provides Python 3.14 + the bench CLI)
#   Node 24 + yarn 1.x (e.g. `nvm install 24 && corepack enable yarn`)
#   MariaDB configured for utf8mb4 (see pharmacyos/dev/mariadb-frappe.cnf) and a root password.
#
# Usage:
#   ERPNEXT_REPO=https://github.com/44mmd/PharmacyOS-ERP ERPNEXT_BRANCH=develop \
#   DB_ROOT_PASSWORD=... ./pharmacyos/dev/setup-dev-bench.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_SRC="${APP_SRC:-$SCRIPT_DIR/../apps/pharmacyos_erp}"
BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
FRAPPE_BRANCH="${FRAPPE_BRANCH:-develop}"
ERPNEXT_REPO="${ERPNEXT_REPO:-https://github.com/44mmd/PharmacyOS-ERP}"
ERPNEXT_BRANCH="${ERPNEXT_BRANCH:-develop}"
DEV_SITE="${DEV_SITE:-pharmacyos.localhost}"
TEST_SITE="${TEST_SITE:-test.localhost}"
DB_ROOT_USER="${DB_ROOT_USER:-root}"
DB_ROOT_PASSWORD="${DB_ROOT_PASSWORD:?set DB_ROOT_PASSWORD (local MariaDB root password)}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}" # local dev only
# PRODUCTION=1 (the pharmacy server installer): no developer mode, no test extras, no development notes
PRODUCTION="${PRODUCTION:-0}"
# optional exact commits for the branches/tags above: a tag that is moved upstream is refused
FRAPPE_COMMIT="${FRAPPE_COMMIT:-}"
ERPNEXT_COMMIT="${ERPNEXT_COMMIT:-}"

if [ "$(id -u)" -eq 0 ]; then
	echo "Run as a non-root user; bench refuses to run as root." >&2
	exit 1
fi

PYTHON_VERSION="${PYTHON_VERSION:-3.14}"           # the server installer pins the patch release
BENCH_CLI_VERSION="${BENCH_CLI_VERSION:-}"         # e.g. 5.31.0; empty = latest
PYTHON_BIN="$(uv python find "$PYTHON_VERSION" 2>/dev/null || true)"
if [ -z "$PYTHON_BIN" ]; then
	uv python install "$PYTHON_VERSION"
	PYTHON_BIN="$(uv python find "$PYTHON_VERSION")"
fi
command -v bench >/dev/null || uv tool install "frappe-bench${BENCH_CLI_VERSION:+==$BENCH_CLI_VERSION}" --python "$PYTHON_VERSION"

node --version | grep -q '^v2[4-9]' || { echo "Node >= 24 required (Frappe develop engines)" >&2; exit 1; }
command -v yarn >/dev/null || { echo "yarn 1.x required (corepack enable yarn)" >&2; exit 1; }

if [ ! -d "$BENCH_DIR" ]; then
	bench init --frappe-branch "$FRAPPE_BRANCH" --python "$PYTHON_BIN" "$BENCH_DIR"
fi
cd "$BENCH_DIR"

[ -d apps/erpnext ] || bench get-app erpnext "$ERPNEXT_REPO" --branch "$ERPNEXT_BRANCH"
for pin in "frappe:$FRAPPE_COMMIT" "erpnext:$ERPNEXT_COMMIT"; do
	app="${pin%%:*}"; want="${pin#*:}"
	[ -z "$want" ] && continue
	have="$(git -C "apps/$app" rev-parse HEAD)"
	[ "$have" = "$want" ] || { echo "$app is at $have, expected the pinned commit $want — refusing to continue." >&2; exit 1; }
done

# `bench new-site` needs redis; run the bench's own redis instances if not already up.
if ! redis-cli -p "$(python3 -c 'import json;print(json.load(open("sites/common_site_config.json"))["redis_cache"].rsplit(":",1)[1])')" ping >/dev/null 2>&1; then
	redis-server config/redis_cache.conf --daemonize yes
	redis-server config/redis_queue.conf --daemonize yes
fi

if [ ! -d "sites/$DEV_SITE" ]; then
	bench new-site "$DEV_SITE" --db-root-username "$DB_ROOT_USER" --db-root-password "$DB_ROOT_PASSWORD" \
		--admin-password "$ADMIN_PASSWORD" --install-app erpnext
	[ "$PRODUCTION" = 1 ] || bench --site "$DEV_SITE" set-config developer_mode 1
	bench use "$DEV_SITE"
fi

# Separate site for automated tests: tests create/delete fixtures and must never run on the dev site.
# Mirrors upstream CI (.github/helper/site_config_mariadb.json installs payments + erpnext and
# server-tests-mariadb.yml warms fixtures with erpnext.tests.bootstrap_test_data).
if [ "${SKIP_TEST_SITE:-0}" != 1 ]; then
[ -d apps/payments ] || bench get-app https://github.com/frappe/payments --branch "$FRAPPE_BRANCH" --skip-assets
if [ ! -d "sites/$TEST_SITE" ]; then
	bench new-site "$TEST_SITE" --db-root-username "$DB_ROOT_USER" --db-root-password "$DB_ROOT_PASSWORD" \
		--admin-password "$ADMIN_PASSWORD" --install-app erpnext --install-app payments
	bench --site "$TEST_SITE" set-config allow_tests true
	bench --site "$TEST_SITE" set-config --parse throttle_user_limit 100  # as upstream CI's test site
	bench --site "$TEST_SITE" run-tests --lightmode --module erpnext.tests.bootstrap_test_data
fi
fi

# PharmacyOS ERP custom app (lives in this repository until it moves to its own).
if [ ! -e apps/pharmacyos_erp ]; then
	ln -sfn "$APP_SRC" apps/pharmacyos_erp
	if [ "$PRODUCTION" = 1 ]; then
		uv pip install -e "apps/pharmacyos_erp" --python env/bin/python
	else
		uv pip install -e "apps/pharmacyos_erp[test]" --python env/bin/python  # [test]: freezegun for the suite
	fi
	grep -qx pharmacyos_erp sites/apps.txt || { [ -n "$(tail -c1 sites/apps.txt)" ] && echo >> sites/apps.txt; echo pharmacyos_erp >> sites/apps.txt; }
fi
SITES=("$DEV_SITE"); [ "${SKIP_TEST_SITE:-0}" = 1 ] || SITES+=("$TEST_SITE")
for site in "${SITES[@]}"; do
	bench --site "$site" list-apps | grep -q pharmacyos_erp || bench --site "$site" install-app pharmacyos_erp
done

bench build

[ "$PRODUCTION" = 1 ] && { echo "Bench ready at $BENCH_DIR"; exit 0; }
cat <<EOF

Bench ready at $BENCH_DIR
  Start:  cd $BENCH_DIR && bench start      (web :8000, socketio :9000, workers, scheduler, redis)
  Desk:   http://$DEV_SITE:8000/desk        (Administrator / \$ADMIN_PASSWORD)
  Tests:  bench --site $TEST_SITE run-tests --lightmode --module erpnext.stock.doctype.batch.test_batch
Next: complete the setup wizard on $DEV_SITE (country Iraq, currency IQD, timezone Asia/Baghdad).
EOF
