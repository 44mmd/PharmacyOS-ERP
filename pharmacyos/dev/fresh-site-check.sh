#!/usr/bin/env bash
# Fresh-install acceptance check (Flow A): new site, NO background worker, first-run setup, then an
# immediate Purchase Receipt and counter sale. Run from the bench directory:
#
#   DB_ROOT_PASSWORD=... pharmacyos/dev/fresh-site-check.sh [site]      (default: fresh-check.localhost)
#
# The site is dropped and recreated: never point this at a site holding real data.
set -euo pipefail
SITE=${1:-fresh-check.localhost}
: "${DB_ROOT_PASSWORD:?set DB_ROOT_PASSWORD to the MariaDB root password}"
if pgrep -f "frappe.utils.bench_helper frappe worker|rq:worker" >/dev/null; then
	echo "A background worker is running: stop it, this check must prove setup works without one." >&2
	exit 1
fi
if [ -d "sites/$SITE" ]; then
	bench drop-site "$SITE" --force --db-root-password "$DB_ROOT_PASSWORD" --no-backup
fi
bench new-site "$SITE" --db-root-username root --db-root-password "$DB_ROOT_PASSWORD" \
	--admin-password "$(openssl rand -hex 12)" --install-app erpnext --install-app pharmacyos_erp
bench --site "$SITE" execute pharmacyos_erp.tests.fresh_site.setup
bench --site "$SITE" execute pharmacyos_erp.tests.fresh_site.check
