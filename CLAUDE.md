# order2homebox

Create Homebox inventory items from Amazon/AliExpress/Temu/Banggood orders and print QR labels
on a Brother QL-500 (DK-22211, 29 mm endless) attached to a Raspberry Pi.

## Architecture

- `server/` — FastAPI web app (runs in a Proxmox LXC). Web UI (Jinja2 + htmx, Apple-style
  CSS, dark mode, DE/EN), single-user login. Scrapes order pages with Playwright using
  session cookies the user imports on the settings page. Talks to the Homebox API and to
  the print agent.
- `printagent/` — small FastAPI service on the Raspberry Pi. Receives a PNG and prints it
  via `brother_ql` (model QL-500, label type "29", `/dev/usb/lp0`). `O2H_DRY_RUN=1` writes
  PNGs to disk instead (used for development).
- `install/` — Proxmox host script (creates the LXC), in-container installer, `update.sh`.
- `docs/PLAN.md` — the approved implementation plan; keep it updated when scope changes.

## Key facts

- Label canvas is **exactly 306 px wide** (29 mm @ 300 dpi); QR content is
  `{HOMEBOX_PUBLIC_URL}/a/{asset_id}`; default layout is 2 identical QR codes side by side
  with the asset ID underneath (see `server/app/labels.py`).
- One scraper file per shop in `server/app/scrapers/` — selectors are constants at the top
  of each file so they are easy to fix when a shop changes its page.
- Playwright is imported lazily (scrapers only); tests never need a browser.
- Config via `.env` (see `server/.env.example`); runtime data in `./data/` (git-ignored).

## Conventions (user preferences)

- All project and Claude Code data stays inside this repository (plans in `docs/`,
  scratch in `.scratch/`, runtime data in `data/`).
- UI strings live in `server/app/locales/{de,en}.json` — never hardcode UI text in
  templates; German is the default language.

## Commands

```sh
# server: install + test + run (from server/)
pip install -e .[dev]
pytest
uvicorn app.main:app --reload

# print agent in dry-run mode (from printagent/)
pip install -e .
O2H_DRY_RUN=1 uvicorn printagent.main:app --port 8010
```
