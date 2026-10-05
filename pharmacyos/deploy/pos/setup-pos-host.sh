#!/usr/bin/env bash
# PharmacyOS POS host: serve the pharmacy ERP's POS screen under its own hostname.
#
#   sudo POS_HOST=pos.halfpharmacy.com SITE=<frappe site> ./setup-pos-host.sh            # HTTPS (Let's Encrypt)
#   sudo POS_HOST=pos.test LISTEN=8080 TLS=off SITE=<frappe site> ./setup-pos-host.sh     # closed test network only
#
# Prerequisites: a PharmacyOS ERP server (deploy/server/install-server.sh) and, for HTTPS, a DNS
# A/AAAA record of POS_HOST pointing at this server, with ports 80 and 443 reachable.
#
# What it does — and nothing else:
#   1. writes /etc/nginx/conf.d/pharmacyos-pos-<host>.conf from pos-host.nginx.conf.template;
#   2. for HTTPS, obtains the certificate with certbot (webroot challenge) and adds HSTS;
#   3. tests and reloads nginx.
# It deliberately does not run `bench setup add-domain`: a later `bench setup nginx` would then write a
# second server block for the same host name. The POS uses relative links only.
# It does not touch the storefront, the admin panel, DNS, the bench's own nginx file or any data.
# Undo: remove the conf file and reload nginx.
set -euo pipefail

POS_HOST="${POS_HOST:?set POS_HOST, e.g. pos.halfpharmacy.com}"
SITE="${SITE:?set SITE (the Frappe site of the pharmacy)}"
BENCH_DIR="${BENCH_DIR:-/home/frappe/frappe-bench}"
UPSTREAM="${UPSTREAM:-127.0.0.1:8000}"
TLS="${TLS:-on}"
LISTEN="${LISTEN:-}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CONF="/etc/nginx/conf.d/pharmacyos-pos-${POS_HOST/_/any}.conf"
UPSTREAM_NAME="pharmacyos_pos_$(echo "${POS_HOST/_/any}" | tr -c 'A-Za-z0-9\n' '_')"  # one per POS host

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
[ -d "$BENCH_DIR/sites/$SITE" ] || { echo "Site $SITE not found in $BENCH_DIR/sites." >&2; exit 1; }
# "_" (any host name) is for a closed test network only
[[ "$POS_HOST" =~ ^(_|[A-Za-z0-9.-]+)$ ]] || { echo "Invalid POS_HOST." >&2; exit 1; }
[ "$POS_HOST" != "_" ] || [ "$TLS" = "off" ] || { echo "POS_HOST=_ needs TLS=off." >&2; exit 1; }

render() { # $1 listen, $2 tls block
	sed -e "s|__POS_HOST__|$POS_HOST|g" -e "s|__SITE__|$SITE|g" -e "s|__SITES__|$BENCH_DIR/sites|g" \
		-e "s|__UPSTREAM_NAME__|$UPSTREAM_NAME|g" -e "s|__UPSTREAM__|$UPSTREAM|g" -e "s|__LISTEN__|$1|g" "$HERE/pos-host.nginx.conf.template" |
		awk -v tls="$2" '{ if ($0 == "__TLS__") print tls; else print }' > "$CONF"
}

if [ "$TLS" = "off" ]; then
	echo "WARNING: plain HTTP. Use only on a closed test network — never across the internet." >&2
	render "${LISTEN:-80}" ""
else
	command -v certbot >/dev/null || apt-get install -y certbot
	mkdir -p /var/www/letsencrypt
	# temporary HTTP server block for the ACME challenge
	cat > "$CONF" <<EOF
server {
	listen 80;
	server_name $POS_HOST;
	location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
	location / { return 301 https://\$host\$request_uri; }
}
EOF
	nginx -t && systemctl reload nginx
	certbot certonly --webroot -w /var/www/letsencrypt -d "$POS_HOST" --non-interactive --agree-tos \
		${CERTBOT_EMAIL:+--email "$CERTBOT_EMAIL"} ${CERTBOT_EMAIL:---register-unsafely-without-email}
	LIVE="/etc/letsencrypt/live/$POS_HOST"
	TLS_BLOCK="	ssl_certificate $LIVE/fullchain.pem;
	ssl_certificate_key $LIVE/privkey.pem;
	ssl_protocols TLSv1.2 TLSv1.3;
	ssl_session_cache shared:pharmacyos_pos:10m;
	ssl_session_tickets off;
	add_header Strict-Transport-Security \"max-age=31536000\" always;"
	render "${LISTEN:-443} ssl" "$TLS_BLOCK"
	# keep the HTTP→HTTPS redirect and the renewal challenge
	cat >> "$CONF" <<EOF

server {
	listen 80;
	server_name $POS_HOST;
	location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
	location / { return 301 https://\$host\$request_uri; }
}
EOF
fi

nginx -t
systemctl reload nginx 2>/dev/null || nginx -s reload
echo "PharmacyOS POS: $( [ "$TLS" = off ] && echo http || echo https )://$POS_HOST$( [ -n "$LISTEN" ] && echo ":$LISTEN" )/pos  (site $SITE)"
