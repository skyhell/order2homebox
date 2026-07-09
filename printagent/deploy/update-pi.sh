#!/usr/bin/env bash
# order2homebox print agent — quick update on the Raspberry Pi:
#   sudo bash /opt/order2homebox/printagent/deploy/update-pi.sh
set -euo pipefail

APP_DIR=/opt/order2homebox
VENV="$APP_DIR/printagent/.venv"

cd "$APP_DIR"
OLD_REV=$(git rev-parse HEAD)
git pull --ff-only
NEW_REV=$(git rev-parse HEAD)

if [ "$OLD_REV" = "$NEW_REV" ]; then
  echo "Already up to date ($(git log --oneline -1))."
  exit 0
fi

if git diff --name-only "$OLD_REV" "$NEW_REV" | grep -q "^printagent/pyproject.toml"; then
  echo "== Dependencies changed — reinstalling =="
  "$VENV/bin/pip" install -q -e "$APP_DIR/printagent"
fi

echo "== Restarting service =="
systemctl restart print-agent
sleep 2
if curl -fsS http://localhost:8010/health >/dev/null; then
  echo "Update OK: $(git log --oneline -1)"
else
  echo "WARNING: agent did not respond on /health — check: journalctl -u print-agent" >&2
  exit 1
fi
