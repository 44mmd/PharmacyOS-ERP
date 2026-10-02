#!/usr/bin/env bash
# PharmacyOS ERP — restore a PharmacyOS backup folder into a site (server administrators only).
#
#   restore-backup.sh <site> <backup-folder> [--with-files] [--yes]
#
# Steps: verify checksums → safety backup of the current site → maintenance mode → restore →
# migrate → health check → maintenance off. The safety backup is skipped only when the site does
# not exist yet (restoring onto a new machine). Requires DB_ROOT_PASSWORD in the environment.
# Encrypted backups are decrypted by Frappe with the site's backup_encryption_key, which must be
# present in the target site's site_config.json (copy it from your offline key record first).
set -euo pipefail

SITE="${1:?usage: restore-backup.sh <site> <backup-folder> [--with-files] [--yes]}"
FOLDER="$(cd "${2:?backup folder required}" && pwd)"
shift 2
WITH_FILES=0; ASSUME_YES=0
for arg in "$@"; do
	case "$arg" in
		--with-files) WITH_FILES=1 ;;
		--yes) ASSUME_YES=1 ;;
		*) echo "unknown option $arg" >&2; exit 2 ;;
	esac
done
BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
: "${DB_ROOT_PASSWORD:?set DB_ROOT_PASSWORD}"
cd "$BENCH_DIR"

[ -f "$FOLDER/metadata.json" ] && [ -f "$FOLDER/database.sql.gz" ] || { echo "Not a PharmacyOS backup folder: $FOLDER" >&2; exit 1; }

echo "== 1/6 Verifying backup"
python3 - "$FOLDER" <<'PY'
import hashlib, json, os, sys
folder = sys.argv[1]
meta = json.load(open(os.path.join(folder, "metadata.json")))
for f in meta["files"]:
	h = hashlib.sha256()
	with open(os.path.join(folder, f["name"]), "rb") as fh:
		for chunk in iter(lambda: fh.read(1 << 20), b""):
			h.update(chunk)
	if h.hexdigest() != f["sha256"]:
		sys.exit(f"checksum mismatch: {f['name']}")
print(f"ok — site {meta['site']}, taken {meta['created_at']}, versions {meta['app_versions']}, encrypted={meta['encrypted']}")
PY

if [ "$ASSUME_YES" != 1 ]; then
	read -r -p "This REPLACES all data of site '$SITE' with the backup. Type the site name to continue: " answer
	[ "$answer" = "$SITE" ] || { echo "Cancelled."; exit 1; }
fi

SITE_EXISTS=0; [ -d "sites/$SITE" ] && SITE_EXISTS=1
if [ "$SITE_EXISTS" = 1 ]; then
	echo "== 2/6 Safety backup of the current state"
	bench --site "$SITE" execute pharmacyos_erp.backup.service.run_backup --kwargs "{'kind': 'Safety'}"
	echo "== 3/6 Maintenance mode on (writes stopped)"
	bench --site "$SITE" set-maintenance-mode on
else
	echo "== 2-3/6 Site does not exist yet — creating an empty site to restore into (no safety backup needed)"
	bench new-site "$SITE" --db-root-password "$DB_ROOT_PASSWORD" \
		--admin-password "$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" >/dev/null
fi

trap '[ "$SITE_EXISTS" = 1 ] && bench --site "$SITE" set-maintenance-mode off || true' EXIT

echo "== 4/6 Restoring"
ARGS=(--db-root-password "$DB_ROOT_PASSWORD" --force)
if [ "$WITH_FILES" = 1 ]; then
	[ -f "$FOLDER/files.tar" ] && ARGS+=(--with-public-files "$FOLDER/files.tar")
	[ -f "$FOLDER/private-files.tar" ] && ARGS+=(--with-private-files "$FOLDER/private-files.tar")
fi
bench --site "$SITE" restore "$FOLDER/database.sql.gz" "${ARGS[@]}"

echo "== 5/6 Migrating to the installed version"
bench --site "$SITE" migrate

echo "== 6/6 Health check"
bench --site "$SITE" set-maintenance-mode off
bench --site "$SITE" execute pharmacyos_erp.backup.service.health_check
bench --site "$SITE" execute pharmacyos_erp.backup.service.record_restore --kwargs "{'folder': '$FOLDER'}"
echo "Restore finished. Restart PharmacyOS services (bench restart / PharmacyOS Server service)."
