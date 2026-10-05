#!/usr/bin/env bash
# PharmacyOS ERP — restore a PharmacyOS backup folder into a site (server administrators only).
#
#   restore-backup.sh <site> <backup-folder> [--with-files] [--yes]
#                     [--create-site] [--keys-file <file>] [--allow-other-site]
#
#   --with-files        also restore the public and private files (daily backups contain them)
#   --yes               do not ask to type the site name (scripts)
#   --create-site       the site does not exist on this bench yet (new machine / blank server):
#                       create it, then restore into it. Without this flag a missing site is an error,
#                       so a wrong BENCH_DIR never silently creates a stray site.
#   --keys-file <file>  the offline copy of the original site's site_config.json (or a JSON file with
#                       its "encryption_key" and "backup_encryption_key"). Needed on a new site: the
#                       backup key decrypts an encrypted backup, the encryption key decrypts the
#                       passwords stored in the database. Keys are never printed.
#   --allow-other-site  the backup was taken from a different site name than <site> (refused otherwise)
#
# Steps: verify the backup (checksums, then — when encrypted — that the key actually decrypts it) →
# safety backup of the current site (or create the new site) → maintenance mode → restore → migrate →
# health check → maintenance off. Everything that can be checked is checked before anything is changed.
# Requires DB_ROOT_PASSWORD in the environment and BENCH_DIR when the bench is not ~/frappe-bench.
set -euo pipefail

usage="usage: restore-backup.sh <site> <backup-folder> [--with-files] [--yes] [--create-site] [--keys-file <file>] [--allow-other-site]"
SITE="${1:?$usage}"
FOLDER="$(cd "${2:?backup folder required}" && pwd)"
shift 2
WITH_FILES=0; ASSUME_YES=0; CREATE_SITE=0; KEYS_FILE=""; OTHER_SITE=0
while [ $# -gt 0 ]; do
	case "$1" in
		--with-files) WITH_FILES=1 ;;
		--yes) ASSUME_YES=1 ;;
		--create-site) CREATE_SITE=1 ;;
		--allow-other-site) OTHER_SITE=1 ;;
		--keys-file) KEYS_FILE="$(cd "$(dirname "${2:?--keys-file needs a file}")" && pwd)/$(basename "$2")"; shift ;;
		*) echo "unknown option $1" >&2; echo "$usage" >&2; exit 2 ;;
	esac
	shift
done
BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
: "${DB_ROOT_PASSWORD:?set DB_ROOT_PASSWORD}"
[ -d "$BENCH_DIR/sites" ] && [ -d "$BENCH_DIR/apps/frappe" ] || { echo "Not a bench: $BENCH_DIR (set BENCH_DIR)." >&2; exit 1; }
cd "$BENCH_DIR"
[ -z "$KEYS_FILE" ] || [ -f "$KEYS_FILE" ] || { echo "Keys file not found: $KEYS_FILE" >&2; exit 1; }
[ -f "$FOLDER/metadata.json" ] && [ -f "$FOLDER/database.sql.gz" ] || { echo "Not a PharmacyOS backup folder: $FOLDER" >&2; exit 1; }

SITE_EXISTS=0; [ -d "sites/$SITE" ] && SITE_EXISTS=1
if [ "$SITE_EXISTS" = 0 ] && [ "$CREATE_SITE" = 0 ]; then
	echo "Site '$SITE' does not exist in $BENCH_DIR/sites. Check BENCH_DIR; to restore onto a new machine, add --create-site (and --keys-file for an encrypted backup)." >&2
	exit 1
fi
if [ "$SITE_EXISTS" = 1 ] && [ "$CREATE_SITE" = 1 ]; then
	echo "Site '$SITE' already exists: --create-site is only for a site that does not exist yet." >&2
	exit 1
fi

echo "== 1/6 Verifying backup"
# checksums, site name and — for an encrypted backup — a trial decryption with the key that will be used
python3 - "$FOLDER" "$SITE" "$SITE_EXISTS" "$OTHER_SITE" "$KEYS_FILE" <<'PY'
import hashlib, json, os, subprocess, sys
folder, site, site_exists, other_site, keys_file = sys.argv[1:6]
meta = json.load(open(os.path.join(folder, "metadata.json")))
for f in meta["files"]:
	path = os.path.join(folder, f["name"])
	if not os.path.exists(path):
		sys.exit(f"Backup damaged: {f['name']} is missing. Use another backup.")
	h = hashlib.sha256()
	with open(path, "rb") as fh:
		for chunk in iter(lambda: fh.read(1 << 20), b""):
			h.update(chunk)
	if h.hexdigest() != f["sha256"]:
		sys.exit(f"Backup damaged: checksum mismatch for {f['name']}. Use another backup.")
print(f"ok — site {meta['site']}, taken {meta['created_at']}, versions {meta['app_versions']}, encrypted={meta['encrypted']}")
if meta["site"] != site and other_site != "1":
	sys.exit(f"This backup is of site '{meta['site']}', not '{site}'. Add --allow-other-site if that is intended.")

def conf(path):
	try:
		return json.load(open(path))
	except FileNotFoundError:
		return {}

keys = conf(keys_file) if keys_file else {}
if site_exists == "1":
	current = {**conf("sites/common_site_config.json"), **conf(f"sites/{site}/site_config.json")}
	for name in ("backup_encryption_key", "encryption_key"):
		if keys.get(name) and current.get(name) and keys[name] != current[name]:
			# the safety backup is taken with the current key; never swap keys under an existing site
			sys.exit(f"The keys file's {name} differs from site '{site}'. Restore into a new site (--create-site) instead.")
	keys = {**keys, **{k: v for k, v in current.items() if k in ("backup_encryption_key", "encryption_key") and v}}
elif not keys.get("encryption_key"):
	# a new site gets a new encryption_key: without the original one, stored passwords are lost
	print("WARNING: no encryption_key given (--keys-file) — passwords stored in the database will not decrypt.", file=sys.stderr)

if meta["encrypted"]:
	key = keys.get("backup_encryption_key")
	if not key:
		sys.exit("The backup is encrypted and no backup_encryption_key is available: pass --keys-file with the original site's keys. Nothing was changed.")
	trial = subprocess.run(
		["gpg", "--batch", "--quiet", "--pinentry-mode", "loopback", "--passphrase-fd", "0", "-o", "-", "-d",
		 os.path.join(folder, "database.sql.gz")],
		input=key.encode() + b"\n", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
	)
	if trial.returncode != 0 or not trial.stdout.startswith(b"\x1f\x8b"):
		sys.exit("The backup_encryption_key does not decrypt this backup (wrong key). Nothing was changed.")
	print("ok — the key decrypts the backup")
PY

if [ "$ASSUME_YES" != 1 ]; then
	read -r -p "This REPLACES all data of site '$SITE' with the backup. Type the site name to continue: " answer
	[ "$answer" = "$SITE" ] || { echo "Cancelled."; exit 1; }
fi

CREATED=0; DONE=0
cleanup() {
	if [ "$SITE_EXISTS" = 1 ]; then
		bench --site "$SITE" set-maintenance-mode off || true
	elif [ "$CREATED" = 1 ] && [ "$DONE" = 0 ]; then
		echo "Restore failed: removing the incomplete new site '$SITE'." >&2
		bench drop-site "$SITE" --db-root-password "$DB_ROOT_PASSWORD" --force --no-backup >/dev/null 2>&1 || true
	fi
}
trap cleanup EXIT

if [ "$SITE_EXISTS" = 1 ]; then
	echo "== 2/6 Safety backup of the current state"
	bench --site "$SITE" execute pharmacyos_erp.backup.service.run_backup --kwargs "{'kind': 'Safety'}"
	echo "== 3/6 Maintenance mode on (writes stopped)"
	bench --site "$SITE" set-maintenance-mode on
else
	echo "== 2-3/6 Creating the new site '$SITE' to restore into (no safety backup needed)"
	CREATED=1
	bench new-site "$SITE" --db-root-password "$DB_ROOT_PASSWORD" \
		--admin-password "$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" >/dev/null
	if [ -n "$KEYS_FILE" ]; then
		# the original site's keys, written without printing them
		python3 - "sites/$SITE/site_config.json" "$KEYS_FILE" <<'PY'
import json, os, sys
target, source = sys.argv[1:3]
config, keys = json.load(open(target)), json.load(open(source))
for name in ("encryption_key", "backup_encryption_key"):
	if keys.get(name):
		config[name] = keys[name]
tmp = target + ".tmp"
with open(tmp, "w") as fh:
	json.dump(config, fh, indent=1, sort_keys=True)
os.chmod(tmp, 0o600)
os.replace(tmp, target)
print("keys written to the new site's configuration")
PY
	fi
fi

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
# Frappe disables the scheduler on restored sites; PharmacyOS needs it for backups and website sync
bench --site "$SITE" enable-scheduler
health="$(bench --site "$SITE" execute pharmacyos_erp.backup.service.health_check)"
echo "$health"
echo "$health" | grep -q '"ok": true' || { echo "Health check FAILED — see above. The restored data is in place; fix before use." >&2; DONE=1; exit 1; }
bench --site "$SITE" execute pharmacyos_erp.backup.service.record_restore --kwargs "{'folder': '$FOLDER'}"
DONE=1
echo "Restore finished. Restart PharmacyOS services (bench restart / PharmacyOS Server service)."
