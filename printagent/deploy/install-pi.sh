#!/usr/bin/env bash
# order2homebox print agent — installer for the Raspberry Pi (Raspberry Pi OS /
# Debian, Brother QL-500 connected via USB). Run as root:
#
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/skyhell/order2homebox/main/printagent/deploy/install-pi.sh)"
#
set -euo pipefail

REPO_URL="${O2H_REPO_URL:-https://github.com/skyhell/order2homebox.git}"
APP_DIR=/opt/order2homebox
VENV="$APP_DIR/printagent/.venv"

echo "== Installing packages =="
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  git python3 python3-venv python3-pip curl ca-certificates >/dev/null

echo "== Cloning $REPO_URL =="
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi

echo "== Python environment =="
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -e "$APP_DIR/printagent"

echo "== Service user + printer permissions (udev) =="
id -u o2h >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin o2h
usermod -aG plugdev o2h
cp "$APP_DIR/printagent/deploy/99-brother-ql.rules" /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger || true

ENV_FILE="$APP_DIR/printagent/.env"
if [ -f "$ENV_FILE" ]; then
  echo "== Keeping existing $ENV_FILE =="
  API_KEY=$(grep '^O2H_PRINT_API_KEY=' "$ENV_FILE" | cut -d= -f2- || true)
else
  API_KEY=$("$VENV/bin/python" -c "import secrets; print(secrets.token_hex(24))")
  cat > "$ENV_FILE" <<EOF
O2H_PRINT_API_KEY=$API_KEY
O2H_PRINTER_MODEL=QL-500
O2H_LABEL_TYPE=29
O2H_PRINTER_DEVICE=/dev/usb/lp0
O2H_PRINTER_BACKEND=linux_kernel
EOF
  chmod 600 "$ENV_FILE"
  chown o2h "$ENV_FILE"
fi

echo "== systemd service =="
cp "$APP_DIR/printagent/deploy/print-agent.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now print-agent

sleep 2
if curl -fsS http://localhost:8010/health >/dev/null; then
  echo
  echo "=============================================================="
  echo " Print agent is up: http://$(hostname -I | awk '{print $1}'):8010"
  echo " API key (set as O2H_PRINT_AGENT_API_KEY on the server):"
  echo "   $API_KEY"
  if [ ! -w /dev/usb/lp0 ] && [ ! -e /dev/usb/lp0 ]; then
    echo " NOTE: /dev/usb/lp0 not found — is the QL-500 connected and powered on?"
  fi
  echo "=============================================================="
else
  echo "WARNING: agent did not respond on /health — check: journalctl -u print-agent" >&2
  exit 1
fi
