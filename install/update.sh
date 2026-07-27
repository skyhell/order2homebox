#!/usr/bin/env bash
# order2homebox — quick update inside the LXC:
#   bash /opt/order2homebox/install/update.sh
# .env and data/ (cookies) are left untouched.
set -euo pipefail

APP_DIR=/opt/order2homebox
VENV="$APP_DIR/server/.venv"

SELF="$APP_DIR/install/update.sh"

cd "$APP_DIR"
# After a re-exec the pull is a no-op, so the revision we started from has to
# be carried over — otherwise the new run would see "no changes" and skip the
# dependency install and the restart.
OLD_REV="${O2H_UPDATE_FROM_REV:-$(git rev-parse HEAD)}"
git pull --ff-only
NEW_REV=$(git rev-parse HEAD)

# The pull may have replaced this very script — bash would keep running the old
# version (and read the new file from its old byte offset), so any step added
# upstream would be skipped until someone happens to run the update twice.
# Hand over to the new version once; O2H_UPDATE_REEXEC stops a loop.
if [ "$OLD_REV" != "$NEW_REV" ] && [ "${O2H_UPDATE_REEXEC:-0}" != "1" ] &&
   git diff --name-only "$OLD_REV" "$NEW_REV" | grep -q "^install/update.sh$"; then
  echo "== update.sh itself changed — continuing with the new version =="
  export O2H_UPDATE_REEXEC=1 O2H_UPDATE_FROM_REV="$OLD_REV"
  exec bash "$SELF" "$@"
fi

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
