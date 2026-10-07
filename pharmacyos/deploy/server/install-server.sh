#!/usr/bin/env bash
# PharmacyOS server installation — the LOCAL ERP's server (Ubuntu 24.04: the PharmacyOS WSL2 environment on
# a single Windows PC, or a pharmacy's Linux server). Production layout: nginx + supervisor (gunicorn,
# workers, scheduler, socket.io), MariaDB, Redis — all started at boot.
#
#   sudo PHARMACY_NAME="Al Noor Pharmacy" PHARMACY_NAME_AR="صيدلية النور" \
#        OWNER_EMAIL=owner@example.com OWNER_PASSWORD=... [OWNER_FULL_NAME=...] [PHARMACY_PHONE=...] \
#        [SITE=pharmacy.local] [HTTP_PORT=80] [PHARMACYOS_DATA_DIR=/mnt/c/ProgramData/PharmacyOS] \
#        ./install-server.sh
#
# The Windows setup (PharmacyOS ERP desktop app → install-server.ps1) runs this inside the PharmacyOS WSL2
# environment; nobody needs to type it.
#
# * Resumable: every step records completion in $STATE_DIR; running the script again (after a reboot, a
#   power cut or a lost connection) continues with the first unfinished step. Steps are idempotent.
# * Progress: lines "##PHARMACYOS-STEP <n> <total> <key>" let the Windows setup show what is happening.
# * Secrets stay inside the server: the database root password and the Administrator password are
#   generated here and kept in /etc/pharmacyos/server.env (root only). Only the owner's password comes
#   from outside, and it is used once, by the first-run setup.
# * Pinned versions: Frappe v16.36.1 / ERPNext v16.37.0, the release the PharmacyOS suite is verified on
#   (the moving version-16 head is not used: v16.50.0 created sites without the Gender DocType).
# * The PharmacyOS ERP app is copied into /opt/pharmacyos/releases/<version>; /opt/pharmacyos/app points
#   at the active release (updates switch it, and switch back on failure — see pharmacyos-server update).
#
# Installing needs the internet once (Ubuntu packages, Node.js, Frappe, ERPNext); running the pharmacy
# afterwards does not.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$(cd "$HERE/../.." && pwd)"   # the PharmacyOS server bundle (or the repository's pharmacyos/ folder)
VERSION="$(cat "$BUNDLE/VERSION" 2>/dev/null || python3 -c "import re,sys;print(re.search(r'__version__\s*=\s*\"([^\"]+)\"',open(sys.argv[1]).read()).group(1))" "$BUNDLE/apps/pharmacyos_erp/pharmacyos_erp/__init__.py")"
BUILD_ID="$(cat "$BUNDLE/BUILD_ID" 2>/dev/null || true)"   # content identity of this bundle (desktop/scripts/prepare-server-bundle.js)
BENCH_USER="${BENCH_USER:-frappe}"
ERPNEXT_REPO="${ERPNEXT_REPO:-https://github.com/frappe/erpnext}"
FRAPPE_BRANCH="${FRAPPE_BRANCH:-v16.36.1}"
ERPNEXT_BRANCH="${ERPNEXT_BRANCH:-v16.37.0}"
# the exact commits of those tags (verified after cloning: a tag moved upstream is refused)
FRAPPE_COMMIT="${FRAPPE_COMMIT:-97a5dd93ca5883bcc9c4ef9834120c5cba397b67}"
ERPNEXT_COMMIT="${ERPNEXT_COMMIT:-af63cde4941570ec7b9e12422c68302762cfcf91}"
NODE_VERSION="${NODE_VERSION:-24.21.0}"  # pinned (Frappe 16 needs Node 24)
PYTHON_VERSION="${PYTHON_VERSION:-3.14.6}"
BENCH_CLI_VERSION="${BENCH_CLI_VERSION:-5.31.0}"
UV_VERSION="${UV_VERSION:-0.11.32}"
ENV_FILE=/etc/pharmacyos/server.env
STATE_DIR=/var/lib/pharmacyos/install
OPT=/opt/pharmacyos
TOTAL=9

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
mkdir -p /etc/pharmacyos "$STATE_DIR" "$OPT/bin" "$OPT/releases"
chmod 700 /etc/pharmacyos

# ---------------------------------------------------------------- configuration (kept across resumes)
if [ -f "$ENV_FILE" ]; then
	# shellcheck disable=SC1090
	. "$ENV_FILE"
fi
SITE="${SITE:-pharmacy.local}"
HTTP_PORT="${HTTP_PORT:-80}"
DATA_DIR="${PHARMACYOS_DATA_DIR:-${DATA_DIR:-/var/lib/pharmacyos/data}}"
DB_ROOT_PASSWORD="${DB_ROOT_PASSWORD:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')}"
umask 077
cat > "$ENV_FILE" <<EOF
# PharmacyOS server — written by install-server.sh. Root only: holds the database root password.
SITE='$SITE'
HTTP_PORT='$HTTP_PORT'
DATA_DIR='$DATA_DIR'
BENCH_USER='$BENCH_USER'
BENCH_DIR='/home/$BENCH_USER/frappe-bench'
DB_ROOT_PASSWORD='$DB_ROOT_PASSWORD'
ADMIN_PASSWORD='$ADMIN_PASSWORD'
FRAPPE_VERSION='$FRAPPE_BRANCH'
ERPNEXT_VERSION='$ERPNEXT_BRANCH'
EOF
umask 022
BENCH_BIN="/home/$BENCH_USER/.local/bin"  # the bench CLI installed by `uv tool install` (uv is in /usr/local/bin)
BENCH_DIR="/home/$BENCH_USER/frappe-bench"

step() { # step <n> <key> <function>
	local n="$1" key="$2" fn="$3"
	echo "##PHARMACYOS-STEP $n $TOTAL $key"
	if [ -f "$STATE_DIR/$key.done" ]; then
		echo "   (already done)"
		return 0
	fi
	"$fn"
	date -Is > "$STATE_DIR/$key.done"
}

as_bench() { su - "$BENCH_USER" -c "export PATH='$BENCH_BIN':\$PATH; $1"; }

# ---------------------------------------------------------------- steps

packages() {
	export DEBIAN_FRONTEND=noninteractive
	apt-get update
	apt-get install -y build-essential git curl ca-certificates gpg mariadb-server mariadb-client libmariadb-dev pkg-config \
		redis-server nginx supervisor cron xvfb libfontconfig1 rsync
}

toolchain() {
	# Node.js: one pinned release from nodejs.org, checked against its published SHA-256 list
	if ! node --version 2>/dev/null | grep -qx "v${NODE_VERSION}"; then
		local tarball="node-v${NODE_VERSION}-linux-x64.tar.xz" tmp
		tmp="$(mktemp -d)"
		curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/${tarball}" -o "$tmp/$tarball"
		curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
		(cd "$tmp" && grep " ${tarball}\$" SHASUMS256.txt | sha256sum -c -)
		rm -rf /opt/node && mkdir -p /opt/node
		tar -xJf "$tmp/$tarball" -C /opt/node --strip-components=1
		for b in node npm npx corepack; do ln -sfn "/opt/node/bin/$b" "/usr/local/bin/$b"; done
		rm -rf "$tmp"
	fi
	command -v yarn >/dev/null || { npm install -g yarn@1.22.22 && ln -sfn /opt/node/bin/yarn /usr/local/bin/yarn; }
	id "$BENCH_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$BENCH_USER"
	chmod 755 "/home/$BENCH_USER"
	# uv (Python 3.14 + the bench CLI): one pinned release, checked against its published SHA-256
	if ! uv --version 2>/dev/null | grep -q "^uv ${UV_VERSION} "; then
		local tmp archive="uv-x86_64-unknown-linux-gnu.tar.gz"
		tmp="$(mktemp -d)"
		curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${archive}" -o "$tmp/$archive"
		curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${archive}.sha256" -o "$tmp/$archive.sha256"
		(cd "$tmp" && sha256sum -c "$archive.sha256")
		tar -xzf "$tmp/$archive" -C "$tmp"
		install -m 0755 "$tmp"/uv-x86_64-unknown-linux-gnu/uv "$tmp"/uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/
		rm -rf "$tmp"
	fi
}

database() {
	install -m 0644 "$BUNDLE/dev/mariadb-frappe.cnf" /etc/mysql/mariadb.conf.d/99-frappe.cnf
	systemctl enable mariadb redis-server
	systemctl restart mariadb
	systemctl start redis-server
	# root: password login for bench (unix_socket stays for the system's own root)
	mariadb -uroot -e "ALTER USER 'root'@'localhost' IDENTIFIED VIA unix_socket OR mysql_native_password USING PASSWORD('${DB_ROOT_PASSWORD}'); FLUSH PRIVILEGES;" \
		|| mariadb -uroot "-p${DB_ROOT_PASSWORD}" -e "SELECT 1" >/dev/null
	mkdir -p "$DATA_DIR" && chown "$BENCH_USER" "$DATA_DIR" || true
	# backups and sales spreadsheets: the pharmacy server's account only (on Windows the folder is on the
	# C: drive and protected by its Windows permissions, set by install-server.ps1)
	chmod 750 "$DATA_DIR" 2>/dev/null || true
}

app_release() {
	# the PharmacyOS ERP app from this bundle, as the active release
	local dest="$OPT/releases/$VERSION${BUILD_ID:+-${BUILD_ID:0:12}}"
	mkdir -p "$dest"
	rsync -a --delete --exclude '__pycache__' --exclude 'node_modules' --exclude 'public/dist' \
		"$BUNDLE/apps/pharmacyos_erp/" "$dest/pharmacyos_erp/"
	chown -R "$BENCH_USER" "$dest"
	ln -sfn "$dest" "$OPT/app"
	echo "$VERSION" > "$OPT/VERSION"
	printf '%s' "$BUILD_ID" > "$OPT/BUILD_ID"
}

bench_and_apps() {
	# a bench left half-built by an interrupted attempt (no working Frappe yet) is built again; this step
	# runs only before the pharmacy exists, so nothing of the pharmacy is lost
	if [ -d "$BENCH_DIR" ] && ! "$BENCH_DIR/env/bin/python" -c "import frappe, erpnext" >/dev/null 2>&1; then
		echo "   rebuilding an incomplete bench"
		rm -rf "$BENCH_DIR"
	fi
	# an interrupted site creation leaves a half site: start it again (nothing was sold on it yet)
	if [ -d "$BENCH_DIR/sites/$SITE" ] && ! as_bench "cd '$BENCH_DIR' && bench --site '$SITE' list-apps" >/dev/null 2>&1; then
		as_bench "cd '$BENCH_DIR' && bench drop-site '$SITE' --db-root-password '$DB_ROOT_PASSWORD' --force --no-backup" || rm -rf "$BENCH_DIR/sites/$SITE"
	fi
	as_bench "PRODUCTION=1 FRAPPE_COMMIT='$FRAPPE_COMMIT' ERPNEXT_COMMIT='$ERPNEXT_COMMIT' \
		FRAPPE_BRANCH='$FRAPPE_BRANCH' ERPNEXT_REPO='$ERPNEXT_REPO' ERPNEXT_BRANCH='$ERPNEXT_BRANCH' \
		PYTHON_VERSION='$PYTHON_VERSION' BENCH_CLI_VERSION='$BENCH_CLI_VERSION' \
		DB_ROOT_PASSWORD='$DB_ROOT_PASSWORD' ADMIN_PASSWORD='$ADMIN_PASSWORD' DEV_SITE='$SITE' SKIP_TEST_SITE=1 \
		APP_SRC='$OPT/app/pharmacyos_erp' bash '$BUNDLE/dev/setup-dev-bench.sh'"
}

site_config() {
	as_bench "cd '$BENCH_DIR' && bench --site '$SITE' set-config pharmacyos_data_dir '$DATA_DIR' \
		&& bench --site '$SITE' set-config developer_mode 0 \
		&& bench --site '$SITE' set-config pharmacyos_server_version '$VERSION' \
		&& bench --site '$SITE' enable-scheduler \
		&& bench --site '$SITE' set-maintenance-mode off \
		&& bench use '$SITE' && bench set-config -g serve_default_site true"
}

first_run() {
	# PharmacyOS first-run setup (company, IQD, Iraq, Arabic, first branch, owner) — no ERPNext wizard.
	# Values travel as a root-only file, never on a command line.
	local cfg
	cfg="$(mktemp)"
	python3 - > "$cfg" <<'PY'
import json, os
print(json.dumps({k: os.environ.get(v) for k, v in {
	"pharmacy_name": "PHARMACY_NAME", "pharmacy_name_ar": "PHARMACY_NAME_AR", "owner_email": "OWNER_EMAIL",
	"owner_password": "OWNER_PASSWORD", "owner_full_name": "OWNER_FULL_NAME", "phone": "PHARMACY_PHONE",
	"address": "PHARMACY_ADDRESS"}.items() if os.environ.get(v)}))
PY
	chown "$BENCH_USER" "$cfg"
	as_bench "cd '$BENCH_DIR' && bench --site '$SITE' execute pharmacyos_erp.setup.first_run.setup_pharmacy --kwargs \"\$(cat '$cfg')\""
	rm -f "$cfg"
}

production() {
	# What `bench setup production` does, without its Ansible/fail2ban prerequisites (it pip-installs
	# Ansible into bench's own environment, which uv builds without pip): supervisor runs gunicorn, the
	# workers, the scheduler, socket.io and the bench's Redis; nginx serves the site on HTTP_PORT.
	cd "$BENCH_DIR"
	if [ "$HTTP_PORT" != 80 ]; then
		as_bench "cd '$BENCH_DIR' && bench set-nginx-port '$SITE' '$HTTP_PORT'"
	fi
	# bench writes the absolute paths of `bench` and `node` into the supervisor config: both must be on PATH
	PATH="$BENCH_BIN:/usr/local/bin:$PATH" "$BENCH_BIN/bench" setup supervisor --user "$BENCH_USER" --yes
	PATH="$BENCH_BIN:/usr/local/bin:$PATH" "$BENCH_BIN/bench" setup nginx --yes --log_format combined   # nginx's built-in access-log format
	if grep -q "None" "$BENCH_DIR/config/supervisor.conf"; then
		echo "supervisor configuration incomplete (a command was not found)" >&2
		exit 1
	fi
	# the bench's Redis instances started for the installation give their ports back to supervisor
	for port in $(python3 -c "import json;c=json.load(open('$BENCH_DIR/sites/common_site_config.json'));print(' '.join(v.rsplit(':',1)[1] for k,v in c.items() if k.startswith('redis_')))"); do
		REDISCLI_AUTH="${REDIS_PASSWORD:-}" redis-cli -p "$port" shutdown nosave >/dev/null 2>&1 || true
	done
	# a kernel without IPv6 (some WSL/VM setups) cannot open [::] sockets: listen on IPv4 only there
	[ -e /proc/net/if_inet6 ] || sed -i '/listen \[::\]/d' "$BENCH_DIR/config/nginx.conf"
	ln -sfn "$BENCH_DIR/config/supervisor.conf" /etc/supervisor/conf.d/frappe-bench.conf
	ln -sfn "$BENCH_DIR/config/nginx.conf" /etc/nginx/conf.d/frappe-bench.conf
	rm -f /etc/nginx/sites-enabled/default
	chown -R "$BENCH_USER:$BENCH_USER" "$BENCH_DIR"
	chmod 755 "/home/$BENCH_USER"
	nginx -t
	systemctl enable nginx supervisor mariadb redis-server
	systemctl restart supervisor
	systemctl restart nginx
	supervisorctl reread >/dev/null || true
	supervisorctl update >/dev/null || true
	install -m 0755 "$HERE/pharmacyos-server" "$OPT/bin/pharmacyos-server"
	install -m 0755 "$BUNDLE/deploy/restore-backup.sh" "$OPT/bin/restore-backup.sh"
	# password on the bench's Redis (reachable from every Windows account through WSL's localhost)
	"$OPT/bin/pharmacyos-server" secure-redis
}

finish() {
	"$OPT/bin/pharmacyos-server" start
	for _ in $(seq 1 60); do
		curl -fsS -m 5 "http://127.0.0.1:$HTTP_PORT/api/method/ping" >/dev/null 2>&1 && break
		sleep 2
	done
	"$OPT/bin/pharmacyos-server" health
}

# ---------------------------------------------------------------- run
: "${PHARMACY_NAME:=}" "${OWNER_EMAIL:=}" "${OWNER_PASSWORD:=}"
if [ ! -f "$STATE_DIR/first_run.done" ] && { [ -z "$PHARMACY_NAME" ] || [ -z "$OWNER_EMAIL" ] || [ -z "$OWNER_PASSWORD" ]; }; then
	echo "PHARMACY_NAME, OWNER_EMAIL and OWNER_PASSWORD are needed for the first installation." >&2
	exit 2
fi
export PHARMACY_NAME PHARMACY_NAME_AR="${PHARMACY_NAME_AR:-}" OWNER_EMAIL OWNER_PASSWORD \
	OWNER_FULL_NAME="${OWNER_FULL_NAME:-}" PHARMACY_PHONE="${PHARMACY_PHONE:-}" PHARMACY_ADDRESS="${PHARMACY_ADDRESS:-}"

step 1 packages packages
step 2 toolchain toolchain
step 3 database database
step 4 app_release app_release
step 5 bench bench_and_apps
step 6 site_config site_config
step 7 first_run first_run
step 8 production production
step 9 finish finish
echo "##PHARMACYOS-DONE $VERSION"
echo "PharmacyOS server ready: http://127.0.0.1:$HTTP_PORT/  (site $SITE)"
echo "IMPORTANT: if you turn on encrypted backups, copy backup_encryption_key from $BENCH_DIR/sites/$SITE/site_config.json to an offline, safe place."
