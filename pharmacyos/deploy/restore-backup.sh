#!/usr/bin/env bash
# PharmacyOS ERP — restore a PharmacyOS backup folder into a site (server administrators only).
#
#   restore-backup.sh <site> <backup-folder> [--with-files] [--yes] [--keep-maintenance]
#
# Steps: verify checksums → safety backup of the current site → maintenance mode → restore →
# migrate → health check → maintenance off. The safety backup is skipped only when the site does
# not exist yet (restoring onto a new machine). Requires DB_ROOT_PASSWORD in the environment.
# Encrypted backups are decrypted by Frappe with the site's backup_encryption_key, which must be
# present in the target site's site_config.json (copy it from your offline key record first).
#
# Exit status (the desktop app and pharmacyos-server report exactly what happened):
#   0  restored
#   3  failed before anything was changed (bad folder, checksum, or the safety backup failed)
#   4  the restore failed and the safety backup was put back automatically (data as before)
#   5  the restore failed and putting the safety backup back failed too: the site STAYS in maintenance
#      mode (no sales on a half-restored database) — restore the safety folder printed above by hand
# --keep-maintenance leaves maintenance mode on after a successful restore (the caller turns it off).
set -Eeuo pipefail

SITE="${1:?usage: restore-backup.sh <site> <backup-folder> [--with-files] [--yes] [--keep-maintenance]}"
FOLDER="$(cd "${2:?backup folder required}" && pwd)" || exit 3
shift 2
WITH_FILES=0; ASSUME_YES=0; KEEP_MAINTENANCE=0
for arg in "$@"; do
	case "$arg" in
		--with-files) WITH_FILES=1 ;;
		--yes) ASSUME_YES=1 ;;
		--keep-maintenance) KEEP_MAINTENANCE=1 ;;
		*) echo "unknown option $arg" >&2; exit 2 ;;
	esac
done
BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
: "${DB_ROOT_PASSWORD:?set DB_ROOT_PASSWORD}"
cd "$BENCH_DIR"

# values reach Python as arguments and as JSON, never pasted into code
kwargs() { python3 -c 'import json, sys; print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2]))))' "$@"; }

[ -f "$FOLDER/metadata.json" ] && [ -f "$FOLDER/database.sql.gz" ] || { echo "Not a PharmacyOS backup folder: $FOLDER" >&2; exit 3; }

echo "== 1/6 Verifying backup"
python3 - "$FOLDER" <<'PY' || exit 3
import hashlib, json, os, sys
folder = sys.argv[1]
meta = json.load(open(os.path.join(folder, "metadata.json")))
for f in meta["files"]:
	name = os.path.basename(f["name"])  # a file of this folder, never a path elsewhere
	h = hashlib.sha256()
	with open(os.path.join(folder, name), "rb") as fh:
		for chunk in iter(lambda: fh.read(1 << 20), b""):
			h.update(chunk)
	if h.hexdigest() != f["sha256"]:
		sys.exit(f"checksum mismatch: {name}")
print(f"ok — site {meta['site']}, taken {meta['created_at']}, versions {meta['app_versions']}, encrypted={meta['encrypted']}")
PY

if [ "$ASSUME_YES" != 1 ]; then
	read -r -p "This REPLACES all data of site '$SITE' with the backup. Type the site name to continue: " answer
	[ "$answer" = "$SITE" ] || { echo "Cancelled."; exit 3; }
fi

restore_db() {
	local args=(--db-root-password "$DB_ROOT_PASSWORD" --force)
	if [ "$2" = 1 ]; then
		[ -f "$1/files.tar" ] && args+=(--with-public-files "$1/files.tar")
		[ -f "$1/private-files.tar" ] && args+=(--with-private-files "$1/private-files.tar")
	fi
	bench --site "$SITE" restore "$1/database.sql.gz" "${args[@]}"
	bench --site "$SITE" migrate
}

SITE_EXISTS=0; [ -d "sites/$SITE" ] && SITE_EXISTS=1
SAFETY=""
if [ "$SITE_EXISTS" = 1 ]; then
	echo "== 2/6 Maintenance mode on (writes stopped)"
	bench --site "$SITE" set-maintenance-mode on || exit 3
	echo "== 3/6 Safety backup of the current state"
	# backup_folder_now raises when the backup could not be verified: then nothing is restored
	if ! SAFETY="$(bench --site "$SITE" execute pharmacyos_erp.backup.service.backup_folder_now --kwargs "$(kwargs with_files 1)" | tail -n 1 | tr -d '"')" \
		|| [ ! -f "$SAFETY/metadata.json" ]; then
		echo "The safety backup failed — nothing was restored, the current data is unchanged." >&2
		bench --site "$SITE" set-maintenance-mode off || true
		exit 3
	fi
	echo "   safety backup: $SAFETY"
else
	echo "== 2-3/6 Site does not exist yet — creating an empty site to restore into (no safety backup needed)"
	bench new-site "$SITE" --db-root-password "$DB_ROOT_PASSWORD" \
		--admin-password "$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" >/dev/null || exit 3
	bench --site "$SITE" set-maintenance-mode on || exit 3
fi

failed() {
	trap - ERR
	echo "== Restore failed" >&2
	if [ -n "$SAFETY" ]; then
		echo "== Putting the safety backup back: $SAFETY" >&2
		if restore_db "$SAFETY" 1; then
			bench --site "$SITE" enable-scheduler || true
			bench --site "$SITE" set-maintenance-mode off || true
			echo "The data is as it was before the restore (safety backup $SAFETY)." >&2
			exit 4
		fi
	fi
	echo "The site stays in maintenance mode. Restore the safety backup by hand: $SAFETY" >&2
	exit 5
}
trap failed ERR

echo "== 4/6 Restoring"
echo "== 5/6 Migrating to the installed version"
restore_db "$FOLDER" "$WITH_FILES"

echo "== 6/6 Health check"
# Frappe disables the scheduler on restored sites; PharmacyOS needs it for backups and website sync
bench --site "$SITE" enable-scheduler
bench --site "$SITE" execute pharmacyos_erp.backup.service.health_check
bench --site "$SITE" execute pharmacyos_erp.backup.service.record_restore --kwargs "$(kwargs folder "$FOLDER")"
trap - ERR
[ "$KEEP_MAINTENANCE" = 1 ] || bench --site "$SITE" set-maintenance-mode off
echo "Restore finished. Restart PharmacyOS services (bench restart / PharmacyOS Server service)."
