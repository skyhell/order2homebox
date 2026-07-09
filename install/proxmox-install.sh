#!/usr/bin/env bash
# order2homebox — Proxmox VE installer.
# Run on the Proxmox HOST as root. Creates a Debian 12 LXC and installs the app:
#
#   bash proxmox-install.sh
#   # or straight from GitHub:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/OWNER/order2homebox/main/install/proxmox-install.sh)"
#
set -euo pipefail

REPO_URL="${O2H_REPO_URL:-https://github.com/OWNER/order2homebox.git}"
RAW_BASE="${O2H_RAW_BASE:-https://raw.githubusercontent.com/OWNER/order2homebox/main}"

if ! command -v pct >/dev/null 2>&1; then
  echo "ERROR: this script must run on a Proxmox VE host (pct not found)." >&2
  exit 1
fi

ask() { # ask <prompt> <default> -> echoes answer
  local answer
  read -r -p "$1 [$2]: " answer
  echo "${answer:-$2}"
}

echo "== order2homebox LXC setup =="
CTID=$(ask "Container ID" "$(pvesh get /cluster/nextid)")
CT_HOSTNAME=$(ask "Hostname" "order2homebox")
STORAGE=$(ask "Storage for rootfs" "local-lvm")
DISK_GB=$(ask "Disk size (GB)" "8")
RAM_MB=$(ask "RAM (MB, Chromium needs headroom)" "2048")
CORES=$(ask "CPU cores" "2")
BRIDGE=$(ask "Network bridge" "vmbr0")

echo
echo "-- Application settings (used to generate the .env) --"
HB_URL=$(ask "Homebox URL" "http://homebox.lan:7745")
HB_USER=$(ask "Homebox username (email)" "")
read -r -s -p "Homebox password: " HB_PASS; echo
PA_URL=$(ask "Print agent URL (Raspberry Pi)" "http://raspberrypi.local:8010")
PA_KEY=$(ask "Print agent API key (from install-pi.sh output, may be empty for now)" "")
WEB_USER=$(ask "Web UI username" "admin")
while :; do
  read -r -s -p "Web UI password: " WEB_PASS; echo
  [ -n "$WEB_PASS" ] && break
  echo "Password must not be empty."
done

echo
echo "== Downloading Debian 12 template =="
pveam update >/dev/null
TEMPLATE=$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)
if [ -z "$TEMPLATE" ]; then
  echo "ERROR: no debian-12-standard template found via pveam." >&2
  exit 1
fi
pveam download local "$TEMPLATE" || true  # no-op if already present

echo "== Creating container $CTID ($CT_HOSTNAME) =="
pct create "$CTID" "local:vztmpl/$TEMPLATE" \
  --hostname "$CT_HOSTNAME" \
  --memory "$RAM_MB" \
  --cores "$CORES" \
  --rootfs "$STORAGE:$DISK_GB" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1

pct start "$CTID"
echo "Waiting for network in the container ..."
for _ in $(seq 1 30); do
  if pct exec "$CTID" -- ping -c1 -W1 deb.debian.org >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "== Installing order2homebox inside the container =="
pct exec "$CTID" -- bash -c "apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null"
pct exec "$CTID" -- env \
  O2H_REPO_URL="$REPO_URL" \
  O2H_SETUP_HOMEBOX_URL="$HB_URL" \
  O2H_SETUP_HOMEBOX_USER="$HB_USER" \
  O2H_SETUP_HOMEBOX_PASS="$HB_PASS" \
  O2H_SETUP_PRINT_AGENT_URL="$PA_URL" \
  O2H_SETUP_PRINT_AGENT_KEY="$PA_KEY" \
  O2H_SETUP_WEB_USER="$WEB_USER" \
  O2H_SETUP_WEB_PASS="$WEB_PASS" \
  bash -c "bash <(curl -fsSL '$RAW_BASE/install/install-in-lxc.sh')"

CT_IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
echo
echo "=============================================================="
echo " order2homebox is running:  http://$CT_IP:8000"
echo " Login: $WEB_USER / (your password)"
echo " Next steps:"
echo "   1. Install the print agent on the Raspberry Pi (install-pi.sh)"
echo "   2. Import shop cookies on the settings page"
echo " Update later with:  pct exec $CTID -- bash /opt/order2homebox/install/update.sh"
echo "=============================================================="
