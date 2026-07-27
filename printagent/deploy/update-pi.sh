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

# Idempotent, and runs before the up-to-date exit so installs from before the
# shutdown button get the rule too.
if [ -f "$APP_DIR/printagent/deploy/o2h-shutdown.sudoers" ]; then
  install -m 0440 -o root -g root \
    "$APP_DIR/printagent/deploy/o2h-shutdown.sudoers" /etc/sudoers.d/o2h-shutdown
  if ! visudo -cf /etc/sudoers.d/o2h-shutdown >/dev/null; then
    rm -f /etc/sudoers.d/o2h-shutdown
    echo "WARNING: sudoers rule rejected — the shutdown button will not work" >&2
  fi
fi

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
