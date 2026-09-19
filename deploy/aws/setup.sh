#!/usr/bin/env bash
# Install the Polymarket L2 + multi-venue collector on a fresh Ubuntu server (observation only: no orders, no
# wallet, no keys). Run from the unpacked bundle:   sudo bash deploy/aws/setup.sh
set -euo pipefail

APP=/opt/polyl2
DATA=/var/lib/polyl2
SRC="$(cd "$(dirname "$0")/../.." && pwd)"

apt-get update -y
apt-get install -y python3-venv python3-pip chrony

id -u polyl2 >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin polyl2
mkdir -p "$APP" "$DATA"
cp -r "$SRC/scripts" "$SRC/src" "$APP/"
find "$APP" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet "aiohttp>=3.9,<4" "aiodns>=3.2,<5"
chown -R polyl2:polyl2 "$APP" "$DATA"

cat > /etc/systemd/system/polyl2.service <<EOF
[Unit]
Description=Polymarket L2 + venue collector (observation only)
After=network-online.target
Wants=network-online.target

[Service]
User=polyl2
WorkingDirectory=$APP
Environment=L2_DIR=$DATA
Environment=L2_MAX_MB=20000
ExecStart=$APP/venv/bin/python scripts/collect_l2.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now polyl2
sleep 25
systemctl --no-pager status polyl2 | head -12
echo
echo "Data directory: $DATA  (cap 20000 MB)."
echo "Check health:   sudo -u polyl2 $APP/venv/bin/python $APP/scripts/health_check.py --dir $DATA --min-hours 1"
