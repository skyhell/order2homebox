#!/usr/bin/env bash
# order2homebox — installer for INSIDE the container/VM (Debian 12/13).
# Called by proxmox-install.sh (settings passed via O2H_SETUP_* env vars),
# but also works standalone on any existing Debian LXC/VM:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/skyhell/order2homebox/main/install/install-in-lxc.sh)"
#
set -euo pipefail

REPO_URL="${O2H_REPO_URL:-https://github.com/skyhell/order2homebox.git}"
APP_DIR=/opt/order2homebox
VENV="$APP_DIR/server/.venv"

ask() { local a; read -r -p "$1 [$2]: " a; echo "${a:-$2}"; }

echo "== Installing packages =="
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  git python3 python3-venv python3-pip fonts-dejavu-core curl ca-certificates >/dev/null

echo "== Cloning $REPO_URL =="
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi

echo "== Python environment =="
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -e "$APP_DIR/server"

echo "== Playwright Chromium (this downloads ~150 MB) =="
"$VENV/bin/playwright" install --with-deps chromium

ENV_FILE="$APP_DIR/server/.env"
if [ -f "$ENV_FILE" ]; then
  echo "== Keeping existing $ENV_FILE =="
else
  echo "== Generating $ENV_FILE =="
  HB_URL="${O2H_SETUP_HOMEBOX_URL:-$(ask 'Homebox URL' 'http://homebox.lan:7745')}"
  HB_USER="${O2H_SETUP_HOMEBOX_USER:-$(ask 'Homebox username (email)' '')}"
  if [ -z "${O2H_SETUP_HOMEBOX_PASS+x}" ]; then
    read -r -s -p "Homebox password: " HB_PASS; echo
  else
    HB_PASS="$O2H_SETUP_HOMEBOX_PASS"
  fi
  PA_URL="${O2H_SETUP_PRINT_AGENT_URL:-$(ask 'Print agent URL' 'http://raspberrypi.local:8010')}"
  PA_KEY="${O2H_SETUP_PRINT_AGENT_KEY:-}"
  WEB_USER="${O2H_SETUP_WEB_USER:-$(ask 'Web UI username' 'admin')}"
  if [ -z "${O2H_SETUP_WEB_PASS+x}" ]; then
    read -r -s -p "Web UI password: " WEB_PASS; echo
  else
    WEB_PASS="$O2H_SETUP_WEB_PASS"
  fi

  WEB_HASH=$("$VENV/bin/python" -m app.hashpw "$WEB_PASS")
  SECRET=$("$VENV/bin/python" -c "import secrets; print(secrets.token_hex(32))")
  # Encrypt the Homebox password (and print-agent key) with a Fernet key kept
  # in server/data/secret.key — so a leaked .env alone does not reveal them.
  cd "$APP_DIR/server"
  HB_PASS_ENC=$("$VENV/bin/python" -m app.encrypt "$HB_PASS")
  if [ -n "$PA_KEY" ]; then
    PA_KEY_ENC=$("$VENV/bin/python" -m app.encrypt "$PA_KEY")
  else
    PA_KEY_ENC=""
  fi
  cd - >/dev/null

  cat > "$ENV_FILE" <<EOF
O2H_HOMEBOX_URL=$HB_URL
O2H_HOMEBOX_PUBLIC_URL=
O2H_HOMEBOX_USERNAME=$HB_USER
O2H_HOMEBOX_PASSWORD=$HB_PASS_ENC
O2H_PRINT_AGENT_URL=$PA_URL
O2H_PRINT_AGENT_API_KEY=$PA_KEY_ENC
O2H_WEB_USER=$WEB_USER
O2H_WEB_PASSWORD_HASH=$WEB_HASH
O2H_SECRET_KEY=$SECRET
O2H_DEFAULT_LANGUAGE=de
EOF
  chmod 600 "$ENV_FILE"
fi

echo "== systemd service =="
cp "$APP_DIR/server/deploy/order2homebox.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now order2homebox

sleep 2
if curl -fsS http://localhost:8000/health >/dev/null; then
  echo "order2homebox is up: http://$(hostname -I | awk '{print $1}'):8000"
else
  echo "WARNING: service did not respond on /health — check: journalctl -u order2homebox" >&2
  exit 1
fi
