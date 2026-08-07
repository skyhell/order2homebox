#!/usr/bin/env bash
# order2homebox — encrypt the plain-text secrets of an existing .env:
#   bash /opt/order2homebox/install/encrypt-env.sh
#
# Fresh installs already store O2H_HOMEBOX_PASSWORD and O2H_PRINT_AGENT_API_KEY
# as "enc:" values; update.sh never touches .env, so an older installation keeps
# its plain text forever. Run this once. Running it again does nothing.
set -euo pipefail

APP_DIR=/opt/order2homebox
VENV="$APP_DIR/server/.venv"
ENV_FILE="$APP_DIR/server/.env"

[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE — nothing to migrate." >&2; exit 1; }

# From server/, because data_dir is relative: the key must be written where the
# service (WorkingDirectory=/opt/order2homebox/server) will look for it.
cd "$APP_DIR/server"
OUTPUT=$("$VENV/bin/python" -m app.encrypt_env "$ENV_FILE")
echo "$OUTPUT"

if echo "$OUTPUT" | grep -q '^Nothing to do'; then
  exit 0
fi

echo "== Restarting service =="
systemctl restart order2homebox
sleep 2
if curl -fsS http://localhost:8000/health >/dev/null; then
  echo "OK — the service is up with encrypted secrets."
  echo
  echo "Two things worth knowing now:"
  echo "  * data/secret.key is required to start. Back it up together with"
  echo "    .env, but keep the two apart — either one alone is useless."
  echo "  * If anything looks wrong, put the old file back:"
  echo "      cp $ENV_FILE.bak $ENV_FILE && systemctl restart order2homebox"
  echo
  echo "Check the settings page: the Homebox row has to stay green (that proves"
  echo "the decrypted password really logs in) and a test print proves the key."
else
  echo "WARNING: service did not respond on /health — check: journalctl -u order2homebox" >&2
  echo "Restore with: cp $ENV_FILE.bak $ENV_FILE && systemctl restart order2homebox" >&2
  exit 1
fi
