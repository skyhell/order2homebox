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
  echo "OK — the service started with encrypted secrets."
  echo "(That only proves it started. Whether the decrypted password still logs"
  echo " into Homebox is step 1 below — the client only connects when used.)"
  echo
  echo "STEP 1 — verify, on the settings page:"
  echo "  * the Homebox row has to stay green — that is the decrypted password"
  echo "    really logging in"
  echo "  * a test print proves the print-agent key"
  echo "  If either fails, put the old file back and nothing is lost:"
  echo "      cp $ENV_FILE.bak $ENV_FILE && systemctl restart order2homebox"
  echo
  echo "STEP 2 — then delete the backup:"
  echo "      shred -u $ENV_FILE.bak"
  echo "  It still holds the secrets in PLAIN TEXT, right next to the file that"
  echo "  was just encrypted. Until it is gone, a snapshot or backup carries the"
  echo "  password anyway — which is the whole point of this exercise."
  echo
  echo "From now on data/secret.key is required to start. Back it up, but keep"
  echo "it apart from .env — either one alone is useless."
else
  echo "WARNING: service did not respond on /health — check: journalctl -u order2homebox" >&2
  echo "Restore with: cp $ENV_FILE.bak $ENV_FILE && systemctl restart order2homebox" >&2
  exit 1
fi
