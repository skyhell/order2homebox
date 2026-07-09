#!/usr/bin/env bash
# order2homebox — quick update inside the LXC:
#   bash /opt/order2homebox/install/update.sh
# .env and data/ (cookies) are left untouched.
set -euo pipefail

APP_DIR=/opt/order2homebox
VENV="$APP_DIR/server/.venv"

cd "$APP_DIR"
OLD_REV=$(git rev-parse HEAD)
git pull --ff-only
NEW_REV=$(git rev-parse HEAD)

if [ "$OLD_REV" = "$NEW_REV" ]; then
  echo "Already up to date ($(git log --oneline -1))."
  exit 0
fi

# Reinstall dependencies only when the dependency definition changed
if git diff --name-only "$OLD_REV" "$NEW_REV" | grep -q "^server/pyproject.toml"; then
  echo "== Dependencies changed — reinstalling =="
  "$VENV/bin/pip" install -q -e "$APP_DIR/server"
  "$VENV/bin/playwright" install chromium
fi

echo "== Restarting service =="
systemctl restart order2homebox
sleep 2
if curl -fsS http://localhost:8000/health >/dev/null; then
  echo "Update OK: $(git log --oneline -1)"
else
  echo "WARNING: service did not respond on /health — check: journalctl -u order2homebox" >&2
  exit 1
fi
