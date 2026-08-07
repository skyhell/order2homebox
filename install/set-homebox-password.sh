#!/usr/bin/env bash
# order2homebox — store a new Homebox password:
#   pct enter <CTID>
#   bash /opt/order2homebox/install/set-homebox-password.sh
#
# Change the password in Homebox itself first, then run this. It asks for the
# new one twice without echoing it, checks it really logs into Homebox BEFORE
# touching .env, encrypts it with the existing key file and restarts the
# service. The password never reaches the shell history or the process list.
set -euo pipefail

APP_DIR=/opt/order2homebox
VENV="$APP_DIR/server/.venv"
ENV_FILE="$APP_DIR/server/.env"

[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE." >&2; exit 1; }

if [ ! -t 0 ]; then
  echo "This needs a terminal: the password is typed, not echoed." >&2
  echo "Enter the container first ('pct enter <CTID>'), then run it there —" >&2
  echo "'pct exec' gives no terminal and the prompt would read nothing." >&2
  exit 1
fi

# From server/, because data_dir is relative: run anywhere else and the token
# would be encrypted with a second, different key file that the service never
# reads — the app would then fail to start.
cd "$APP_DIR/server"
"$VENV/bin/python" -m app.set_secret O2H_HOMEBOX_PASSWORD "$ENV_FILE"

echo "== Restarting service =="
systemctl restart order2homebox
sleep 2
if curl -fsS http://localhost:8000/health >/dev/null; then
  echo "Done — the new password is in place and Homebox accepted it."
else
  echo "WARNING: service did not respond on /health — check: journalctl -u order2homebox" >&2
  exit 1
fi
