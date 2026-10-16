#!/usr/bin/env bash
# One-time setup of the VivaVerify backend on an Ubuntu VM (22.04+ / Python 3.10+).
# Run from the vivaverify/ folder after copying it to the VM and placing backend/.env:
#   sudo DOMAIN=yourname.dynu.net EMAIL=you@example.com bash backend/deploy/setup_vm.sh
# Before running: the Dynu hostname must point to this VM's public IP and ports 80/443 must be open.
set -euo pipefail
: "${DOMAIN:?set DOMAIN=your Dynu hostname}"
: "${EMAIL:?set EMAIL for Let's Encrypt}"
APP=/opt/vivaverify

apt-get update
apt-get install -y python3-venv python3-pip nginx certbot python3-certbot-nginx rsync

id -u vivaverify >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin vivaverify
mkdir -p "$APP/backend"
rsync -a --delete --exclude .venv --exclude __pycache__ --exclude .pytest_cache backend/ "$APP/backend/"
test -f "$APP/backend/.env" || { echo "backend/.env is missing - copy it before running this script"; exit 1; }

python3 -m venv "$APP/backend/.venv"
"$APP/backend/.venv/bin/pip" install -q --upgrade pip
"$APP/backend/.venv/bin/pip" install -q -r "$APP/backend/requirements.txt"
chown -R vivaverify: "$APP"
chmod 600 "$APP/backend/.env"

cp backend/deploy/vivaverify.service /etc/systemd/system/vivaverify.service
systemctl daemon-reload
systemctl enable --now vivaverify

sed "s/YOUR_DOMAIN/$DOMAIN/" backend/deploy/nginx.conf > /etc/nginx/sites-available/vivaverify
ln -sf /etc/nginx/sites-available/vivaverify /etc/nginx/sites-enabled/vivaverify
nginx -t && systemctl reload nginx

certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect
curl -fsS "https://$DOMAIN/api/health" && echo && echo "Backend is live at https://$DOMAIN"
