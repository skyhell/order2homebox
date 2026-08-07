# Changelog

All notable changes to this project are documented here. Versions follow the
repository's convention: a user-visible feature gets a minor bump, a pure
bug-fix or maintenance release a patch bump.

Each version links to its GitHub release, which carries the full notes.

## [Unreleased]

### Fixed

- **The Homebox row on the settings page follows the connection again.** It
  stopped asking as soon as it was green, so it kept showing a state that could
  be hours old. The reason it had to stay quiet was `status()`: it called
  `_login()` directly, paying for a full login on every check. It now runs
  through the normal request path and checks with `GET /api/v1/users/self`,
  which reuses the cached token — a check costs one small request, a login only
  once the token expires. Both rows now poll in every state (Homebox every 30 s,
  the print agent every 10 s). A Homebox that does not serve that endpoint falls
  back to the old login check instead of reporting the connection as broken.

## [0.8.0] — 2026-07-29

### Added

- **"Apply to all cards" button** next to *+ New location* on every item card.
  One click copies a card's location to all the others, including a location
  just created inline — the other cards learn the new option first, otherwise
  they would silently keep their old one. The button only appears once a second
  card exists.

### Fixed

- **Updates reach the browser.** Static files are served without a
  `Cache-Control` header, so a browser could keep an old `app.js` for hours
  after an update and quietly run the previous version of a fix. `app.css`,
  `app.js` and `htmx.min.js` are now stamped with the file's mtime, which a
  `git pull` rewrites for every file it changes. The app version cannot serve
  as the stamp — fixes ship between releases, so two different files would
  share one URL.

## [0.7.0] — 2026-07-29

### Added

- **Per-item asset-ID checkbox on the edit page.** The label printed straight
  after creating an item always followed `O2H_LABEL_SHOW_ASSET_ID`; the only
  way to get a bare QR code was to change that setting for everything. The
  setting is now just the checkbox's default.
- The choice travels with the created item, so the **result card shows what was
  actually printed** — its checkbox and preview follow the item's setting
  instead of the global default.

## [0.6.0] — 2026-07-27

### Added

- **The location last used is pre-selected for every item of the next order.**
  It applies to all cards, not just the first, and to manual entry as well.

  The choice lives in `data/prefs.json` (new `app/prefs.py`), so it survives
  restarts and updates — `update.sh` never touches `data/`. It is written only
  once an item was really created in Homebox: a failed creation must not leave
  you with a location nothing ever landed in. A missing, corrupt or unwritable
  preference file is swallowed, because losing a pre-selection may never break
  creating items.

## [0.5.0] — 2026-07-27

### Added

- **Shut down Pi** button on the settings page, behind a confirmation prompt.
  The agent gains `POST /shutdown` protected by the existing API key.
  Privileges stay narrow: a sudoers drop-in lets the service user run exactly
  `/usr/sbin/shutdown -h now` as root — no shell, no reboot, no caller-supplied
  arguments. Both deploy scripts install it, validate it with `visudo` and
  remove it again if it is rejected, because a broken drop-in would lock `sudo`
  for everyone.
- **Connection rows update themselves.** The print-agent row polls every 10 s.
  The Homebox row only retries while it is broken — each of its checks is a
  full login, unlike the agent's cheap `/health`.

### Fixed

- **The settings page hung on an unreachable host.** Rendering awaited both
  connection checks, and a switched-off machine answers with nothing at all, so
  the request sat out the full timeout before any HTML was sent — measured at
  5.4 s, against 0.02 s now with the same dead agent.
- **The status could be served from the browser cache.** The polled fragment is
  now `no-store`.
- **"Pi is shutting down" never went away.** It now clears as soon as the agent
  stops answering — while the Pi is still going down it keeps replying, so the
  note survives exactly as long as it is true.
- **Test print and shutdown are hidden while the agent is unreachable** — both
  could only fail there.
- **The update scripts could not update themselves.** They pulled and then kept
  running the old copy, so a step added upstream was silently skipped until
  someone happened to run the update twice — which is how the shutdown sudoers
  rule went missing on the first try. They now hand over to their new version
  once, carrying the starting revision so the second run still installs
  dependencies and restarts the service.

## [0.4.2] — 2026-07-27

### Fixed

- **The per-item *Print label* button printed the wrong label.** A result
  card's print controls sit inside `#create-form`, and htmx adds the enclosing
  form's fields to every POST — where they even override `hx-include`. With
  identical input names in every card, `/print` received `asset_id` and
  `show_text` once per card and Starlette's `FormData` kept the last one, so
  any card's button printed the **last** card's asset id with the last card's
  checkbox state. The controls are now nameless and pass their values via
  `hx-vals`, which does win over the form values.

### Changed

- The label preview follows its *print asset ID* checkbox, so the picture
  always shows what the printer would produce.
- `install-pi.md` documents two pitfalls found while setting up a real Pi: the
  installer's first run can time out on its own health check while the venv is
  still building wheels (run it again — the API key is kept), and `.local`/mDNS
  does not resolve from a minimal Debian LXC.

## [0.4.1] — 2026-07-23

### Changed

- Maintenance only, no behaviour change: silenced two test deprecation warnings
  (Pillow `getdata()` → `tobytes()`, and `httpx2` as a dev dependency because
  Starlette's `TestClient` prefers it). Suite runs clean.
- Docs: UI screenshots added, Banggood scraper documented.

## [0.4.0] — 2026-07-22

### Added

- **Banggood** as a fourth shop, alongside Amazon, AliExpress and Temu. New
  self-contained scraper for the account order-detail page, verified against a
  real order. The purchase price is the grand total actually paid — after
  discounts and including shipping — split proportionally across items. A wrong
  order id or half-expired session redirects to the order list and raises
  `OrderNotFound`. Banggood appears automatically in the shop dropdown, cookie
  settings and error messages, because the UI is data-driven off the shop list.

## [0.3.0] — 2026-07-10

### Added

- **Animated dark/light toggle** — a circular wipe expanding from the toggle
  button (View Transitions API) plus an icon pop, falling back to a colour
  cross-fade, respecting `prefers-reduced-motion`.
- **Footer with version and docs link** on every page, read from the package
  metadata.

## [0.2.0] — 2026-07-10

### Added

- **Print a label from a Homebox link.** A *Print label* page takes an
  `…/a/{asset_id}` deep link, an `…/item/{uuid}` page URL (resolved via the
  API) or a bare asset ID, and reprints its label without going through an
  order. The printed QR always encodes the short, stable `…/a/{asset_id}` link,
  even when an item URL was pasted.

## [0.1.0] — 2026-07-10

First release. Create Homebox inventory items from Amazon, AliExpress and Temu
orders and print QR labels on a Brother QL-500 (DK-22211, 29 mm endless)
attached to a Raspberry Pi. All three scrapers verified end to end against real
orders.

- **Order scraping** from the order page using session cookies imported on the
  settings page, one scraper file per shop. Amazon with foreign-VAT price
  correction, AliExpress with per-character price spans and German day-first
  dates, Temu from the embedded `rawData` JSON.
- **Review & edit** before anything is created, with a per-item "create &
  print" button as well as a bulk create.
- **Homebox integration** — items with purchase details and the order number,
  supporting both the legacy and the new entities API (auto-detected).
  Locations are read live and new ones can be created inline.
- **QR labels** — exactly 306 px wide (29 mm @ 300 dpi), two QR codes side by
  side with the asset ID underneath, printed by a print agent on the Pi
  (`brother_ql`). Dry-run mode writes PNGs for development.
- **Web UI** — single-user login, Apple-style design with a dark-mode toggle
  and a DE/EN switch (German default).
- **Encrypted secrets (opt-in)** — `enc:…` values in `.env` with the key kept
  in `data/secret.key`.
- **Deployment** — Proxmox host script that creates the LXC, in-container
  installer and `update.sh`, plus `install-pi.sh` / `update-pi.sh` for the Pi.

[0.8.0]: https://github.com/skyhell/order2homebox/releases/tag/v0.8.0
[0.7.0]: https://github.com/skyhell/order2homebox/releases/tag/v0.7.0
[0.6.0]: https://github.com/skyhell/order2homebox/releases/tag/v0.6.0
[0.5.0]: https://github.com/skyhell/order2homebox/releases/tag/v0.5.0
[0.4.2]: https://github.com/skyhell/order2homebox/releases/tag/v0.4.2
[0.4.1]: https://github.com/skyhell/order2homebox/releases/tag/v0.4.1
[0.4.0]: https://github.com/skyhell/order2homebox/releases/tag/v0.4.0
[0.3.0]: https://github.com/skyhell/order2homebox/releases/tag/v0.3.0
[0.2.0]: https://github.com/skyhell/order2homebox/releases/tag/v0.2.0
[0.1.0]: https://github.com/skyhell/order2homebox/releases/tag/v0.1.0
