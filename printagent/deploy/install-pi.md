# Print agent on the Raspberry Pi

The print agent drives the Brother QL-500 (DK-22211, 29 mm endless) and exposes
a small HTTP API that the order2homebox server calls.

## Install

Connect the QL-500 via USB, power it on, then run on the Pi:

```sh
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/OWNER/order2homebox/main/printagent/deploy/install-pi.sh)"
```

The script installs everything (clone → venv → udev rule → systemd service) and
prints the generated **API key** at the end. Enter that key on the server as
`O2H_PRINT_AGENT_API_KEY` (in `/opt/order2homebox/server/.env`), then restart the
server: `systemctl restart order2homebox`.

## Update

```sh
sudo bash /opt/order2homebox/printagent/deploy/update-pi.sh
```

## Troubleshooting

- **`/dev/usb/lp0` missing** — check cable/power, then `dmesg | grep -i usblp`.
  The `usblp` kernel module must be loaded (it is by default on Raspberry Pi OS).
- **Permission denied on the device** — re-plug the printer after installation so
  the udev rule (`/etc/udev/rules.d/99-brother-ql.rules`) applies, or run
  `sudo udevadm trigger`.
- **Test without a printer** — add `O2H_DRY_RUN=1` to
  `/opt/order2homebox/printagent/.env` and restart; labels are written as PNG
  files instead of being printed.
- **Logs** — `journalctl -u print-agent -f`
