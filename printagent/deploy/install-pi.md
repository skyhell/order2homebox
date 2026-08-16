# Print agent on the Raspberry Pi

The print agent drives the Brother QL-500 (DK-22211, 29 mm endless) and exposes
a small HTTP API that the order2homebox server calls.

## Install

Connect the QL-500 via USB, power it on, then run on the Pi:

```sh
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/skyhell/order2homebox/main/printagent/deploy/install-pi.sh)"
```

The script installs everything (clone → venv → udev rule → systemd service) and
prints the generated **API key** at the end. Enter that key on the server as
`O2H_PRINT_AGENT_API_KEY` (in `/opt/order2homebox/server/.env`) together with
`O2H_PRINT_AGENT_URL=http://<pi>:8010`, then restart the server:
`systemctl restart order2homebox`.

That is the way to set up the *first* printer. **Every further Pi** runs the
same script and is then added on the app's settings page (*Printers → Add a
printer*) with the key it printed — no `.env` edit and no restart. Each browser
chooses there which of the printers it prints on and remembers it.

Give the Pi a fixed address before you write that URL — a DHCP reservation in
your router, then either its IP or its hostname works. Plain `.local` (mDNS)
usually does **not** resolve from a minimal Debian LXC, which has no
`libnss-mdns`; check with `curl http://<pi>:8010/health` from inside the
container before relying on a name.

## Shutting the Pi down

The settings page of the server has a **Shut down Pi** button (with a
confirmation prompt) that calls `POST /shutdown` on the agent. Wait until the
green LED stays off before cutting the power.

The installer allows this by dropping `/etc/sudoers.d/o2h-shutdown`, which lets
the service user run exactly one command as root:

```
o2h ALL=(root) NOPASSWD: /usr/sbin/shutdown -h now
```

No shell, no reboot, no caller-supplied arguments. `update-pi.sh` installs the
rule as well, so an agent set up before this feature picks it up on the next
update. If the button reports a failure, check that the file exists and that
`shutdown` really lives at `/usr/sbin/shutdown` on your image — the path in the
sudoers rule and in `O2H_SHUTDOWN_CMD` must match exactly.

## Update

```sh
sudo bash /opt/order2homebox/printagent/deploy/update-pi.sh
```

## Troubleshooting

- **First run ends with „agent did not respond on /health"** — harmless on a
  slower Pi: the script waits only 2 s, while the venv is often still building
  its wheels. Simply run the installer again. It keeps the existing `.env`, so
  the API key stays the same and is printed again.
- **`/dev/usb/lp0` missing** — check cable/power, then `dmesg | grep -i usblp`.
  The `usblp` kernel module must be loaded (it is by default on Raspberry Pi OS).
- **Permission denied on the device** — re-plug the printer after installation so
  the udev rule (`/etc/udev/rules.d/99-brother-ql.rules`) applies, or run
  `sudo udevadm trigger`.
- **Test without a printer** — add `O2H_DRY_RUN=1` to
  `/opt/order2homebox/printagent/.env` and restart; labels are written as PNG
  files instead of being printed.
- **Logs** — `journalctl -u print-agent -f`
