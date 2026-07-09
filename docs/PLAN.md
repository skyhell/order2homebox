# order2homebox — Plan

## Context

Neues Greenfield-Projekt in `C:\Users\johan\claude\order2homebox`. Ziel: Bestellungen von AliExpress, Temu oder Amazon per **Bestellnummer** als Homebox-Assets erfassen und sofort ein QR-Etikett drucken. Ablauf: Shop + Bestellnummer eingeben → **Scraper holt die Bestelldaten von der Order-Detailseite** (mit importierten Session-Cookies) → Daten in der Web-UI editieren (inkl. Lagerort-Auswahl/-Anlage) → Item per Homebox-API anlegen → QR-Label (2 QR nebeneinander, optional Asset-ID darunter) auf Brother QL-500 mit DK-22211 (29 mm endlos) drucken. Drucker per USB am Raspberry Pi; App im Proxmox-LXC; Veröffentlichung auf GitHub.

**Geklärte Entscheidungen (User):**
- Datenbeschaffung: **Scraping** der Order-Detailseite, ein eigenständiger Scraper pro Shop (leicht änderbar). Session via **Cookie-Import**: User exportiert Cookies per Browser-Extension (z. B. Cookie-Editor) und fügt sie in der Settings-Seite ein.
- Label: **2 QR-Codes nebeneinander über die 29-mm-Breite** (je ~14 mm), optional Asset-ID darunter.
- Stack: **Python + FastAPI** (Server-UI mit Jinja2 + htmx; Print-Agent ebenfalls Python).
- Lagerorte: live aus Homebox lesen, im Formular wählbar, **neue Lagerorte direkt anlegbar**.
- **Proxmox-Installscript inkl. LXC-Erstellung** (auf dem Proxmox-Host ausführbar).
- **Login für die Web-UI** (Single-User).
- UI im **Apple-Stil mit Dark-Mode-Umschaltung**; Sprache **DE/EN umschaltbar**.

**Recherchierte Fakten:**
- `brother_ql` (pklaus) unterstützt QL-500; 29-mm-Endlos = Label-Typ `"29"`, **306 px druckbare Breite** bei 300 dpi, variable Länge. QL-500 hat keinen Auto-Cutter.
- Homebox-QR-Codes kodieren `{base_url}/a/{asset_id}` (z. B. `/a/000-001`).
- Homebox-API: `POST /api/v1/users/login` (Bearer-Token), `GET/POST /api/v1/locations`, `GET /api/v1/labels`, `POST /api/v1/items`, `PUT /api/v1/items/{id}` für Kaufinfos (purchasePrice, purchaseFrom, purchaseTime). Asset-ID wird automatisch vergeben (`assetId`). (Feldnamen bei Implementierung gegen Swagger `/swagger/index.html` der Live-Instanz verifizieren.)
- Amazon/AliExpress/Temu haben keine öffentliche Bestell-API → Scraping mit Session-Cookies; Bot-Erkennung (bes. Amazon) erfordert echten Browser → **Playwright headless Chromium**.

## Architektur

```
┌─ Proxmox LXC ────────────────────────┐      ┌─ Raspberry Pi ─────────────┐
│ order2homebox-server (FastAPI)       │ HTTP │ print-agent (FastAPI)      │
│  Web-UI (Jinja2+htmx, Login, DE/EN,  │─────▶│  brother_ql → /dev/usb/lp0 │
│         Apple-Style + Dark Mode)     │      │  QL-500, DK-22211          │
│  Scraper: Playwright + Cookies       │      └────────────────────────────┘
│   (amazon.py / aliexpress.py /       │
│    temu.py — je 1 Datei pro Shop)    │
│  Homebox-Client (httpx)              │
│  Label-Renderer (Pillow + segno)     │
│  data/: cookies je Shop (JSON, 600)  │
└───────────┬──────────────────────────┘
            │ HTTP (Bearer)
            ▼
        Homebox-API
```

Kein eigener Datenbestand außer `data/` (Shop-Cookies, UI-Einstellungen) — Homebox bleibt die Quelle der Wahrheit. Konfiguration per `.env`.

## Daten-Ablage (alles im Projektverzeichnis)

Sämtliche Projekt- und Claude-Code-Daten liegen im Projektverzeichnis `C:\Users\johan\claude\order2homebox` (nicht in globalen Verzeichnissen):
- `CLAUDE.md` im Repo-Root (Projektkontext für Claude Code) + `.claude/settings.json` im Repo für projektspezifische Einstellungen.
- Dieser Plan wird beim Implementierungsstart als `docs/PLAN.md` ins Repo übernommen und dort gepflegt.
- App-Laufzeitdaten: `DATA_DIR` zeigt per Default auf `./data` im App-Verzeichnis (Cookies, UI-Einstellungen); `.env` liegt daneben. Beides in `.gitignore`. Im LXC/Pi entsprechend im jeweiligen App-Verzeichnis unter `/opt/order2homebox`.
- Scratch-/Zwischendateien während der Entwicklung: `./.scratch/` im Projekt (in `.gitignore`).

## Repository-Layout

```
order2homebox/
├── README.md                 # EN, Architektur, Setup, Screenshots-Platzhalter
├── LICENSE                   # MIT
├── .gitignore                # inkl. data/, .env
├── server/
│   ├── pyproject.toml        # fastapi, uvicorn, httpx, jinja2, playwright, segno, pillow,
│   │                         # pydantic-settings, itsdangerous, passlib[bcrypt]
│   ├── app/
│   │   ├── main.py           # FastAPI-App, Routen, SessionMiddleware
│   │   ├── config.py         # HOMEBOX_URL, HOMEBOX_USER/PASS, PRINT_AGENT_URL/KEY,
│   │   │                     # WEB_USER, WEB_PASSWORD_HASH, SECRET_KEY, DATA_DIR, LABEL_*
│   │   ├── auth.py           # Login-Route, Session-Cookie (signiert), Dependency require_login
│   │   ├── i18n.py           # Übersetzungen aus locales/{de,en}.json, Jinja2-Helper t(),
│   │   │                     # Umschaltung per Session + Toggle in der Navbar (Default DE)
│   │   ├── homebox.py        # API-Client: Login/Token-Refresh, Locations lesen+anlegen,
│   │   │                     # Labels, create_item, update_item (Kaufinfos)
│   │   ├── models.py         # Order, OrderItem-Draft (name, qty, preis, bestellnr, shop, …)
│   │   ├── scrapers/
│   │   │   ├── __init__.py   # Registry: get_scraper(shop)
│   │   │   ├── base.py       # Scraper-Basisklasse: Playwright-Context mit Cookies laden,
│   │   │   │                 # fetch_order(order_no) -> Order; Fehlerklassen
│   │   │   │                 # (SessionExpired → UI: „Cookies erneuern“, OrderNotFound)
│   │   │   ├── amazon.py     # Order-URL-Template + DOM-Selektoren, alles in 1 Datei,
│   │   │   ├── aliexpress.py #  Selektoren als Konstanten am Dateianfang, gut kommentiert
│   │   │   └── temu.py       #  → bei Seiten-Änderung leicht anzupassen
│   │   ├── cookies.py        # Import/Speicherung der Shop-Cookies (JSON aus Cookie-Editor),
│   │   │                     # Validierung, Status je Shop (vorhanden/abgelaufen)
│   │   ├── labels.py         # PNG: 306 px breit, 2× QR (segno) nebeneinander,
│   │   │                     # optional Asset-ID darunter (Pillow + mitgelieferter TTF)
│   │   ├── printer.py        # POST PNG an Print-Agent, Health-Check
│   │   ├── locales/de.json, en.json
│   │   ├── templates/        # base (Navbar: Sprache, Dark-Mode, Logout), login, index
│   │   │                     # (Shop+Bestellnr.), edit (Artikel-Formulare), result
│   │   │                     # (Asset-IDs, Label-Vorschau, Druck), settings (Cookies je Shop,
│   │   │                     # Status Homebox/Print-Agent, Testdruck)
│   │   └── static/app.css    # Apple-Design: -apple-system-Fontstack, Cards, CSS-Variablen;
│   │       app.js            # Dark-Mode: prefers-color-scheme + manueller Toggle (localStorage)
│   ├── tests/                # pytest: Scraper-Parsing gegen eingecheckte HTML-Fixtures
│   │                         # (Playwright-Fetch gemockt), Label-Renderer (306 px, QR
│   │                         # dekodierbar via segno), Homebox-Client (respx), Auth
│   └── deploy/order2homebox.service
├── printagent/
│   ├── pyproject.toml        # fastapi, uvicorn, brother_ql, pillow
│   ├── printagent/main.py    # POST /print (PNG+copies, X-Api-Key), GET /health,
│   │                         # --dry-run schreibt PNG statt zu drucken
│   └── deploy/
│       ├── install-pi.sh     # Install-Script auf dem Pi: apt-Pakete, git clone, venv,
│       │                     # udev-Regel + Gruppe einrichten, API-Key generieren,
│       │                     # systemd enable/start, Testdruck-Hinweis
│       ├── update-pi.sh      # Schnelles Update: git pull, Deps bei Bedarf, restart, Health-Check
│       ├── print-agent.service
│       ├── 99-brother-ql.rules  # udev USB 04f9:2015
│       └── install-pi.md     # Kurzdoku: nur curl|bash-Aufruf von install-pi.sh + Troubleshooting
├── install/
│   ├── proxmox-install.sh    # Auf dem Proxmox-Host: fragt/setzt CTID, erstellt Debian-12-LXC
│   │                         # (pct create, 2 GB RAM für Chromium), installiert Python-venv,
│   │                         # App von GitHub, playwright install --with-deps chromium,
│   │                         # generiert .env (SECRET_KEY, Passwort-Hash interaktiv),
│   │                         # systemd-Unit, zeigt am Ende die URL an
│   ├── install-in-lxc.sh     # Teil 2, läuft im Container (wird von Teil 1 per pct exec genutzt,
│   │                         # aber auch standalone für bestehende LXC/VM verwendbar)
│   └── update.sh             # Schnelles Update im Container: git pull, Dependencies nur bei
│                             # Änderung nachinstallieren, systemctl restart, Version anzeigen;
│                             # analoges update-pi.sh für den Print-Agent auf dem Raspberry Pi
└── docs/label-layout.md      # Maße, Beispielbild
```

## Kern-Designs

### Scraper (je Shop 1 Datei, leicht änderbar)
- `base.py`: startet Playwright headless Chromium, injiziert die gespeicherten Cookies des Shops, lädt `ORDER_URL_TEMPLATE.format(order_no=…)`, wartet auf Selektor, übergibt HTML an `parse(html) -> Order`.
- Jede Shop-Datei enthält oben nur Konstanten (`ORDER_URL_TEMPLATE`, CSS-Selektoren) und eine `parse()`-Funktion — Änderungen bei Seiten-Updates bleiben auf eine Datei begrenzt.
  - Amazon: `https://www.amazon.de/gp/your-account/order-details?orderID={…}` (Bestellnr. `\d{3}-\d{7}-\d{7}`)
  - AliExpress: `https://www.aliexpress.com/p/order/detail.html?orderId={…}`
  - Temu: `https://www.temu.com/bgt_order_detail.html?parent_order_sn={…}`
- Erkennung „nicht eingeloggt“ (Login-Redirect/Selektor) → `SessionExpired` → UI verlinkt auf Settings („Cookies für Amazon erneuern“).
- Extrahiert je Artikel: Name, Menge, Einzelpreis, Produkt-URL; plus Bestellnummer/-datum → `purchaseFrom`, `purchaseTime`, Beschreibung.

### Cookie-Import (Settings-Seite)
- Pro Shop ein Textfeld: JSON-Export der Cookie-Editor-Extension einfügen → Validierung → Speicherung als `data/cookies/{shop}.json` (chmod 600).
- Statusanzeige je Shop (importiert am, letzter erfolgreicher Fetch, abgelaufen-Flag) + Verbindungstest Homebox/Print-Agent + Testdruck-Button.

### Web-Login, Apple-Style, i18n
- Single-User-Login: `WEB_USER` + bcrypt-Hash in `.env` (Installscript fragt Passwort ab und hasht); signiertes Session-Cookie (itsdangerous), `require_login`-Dependency auf allen Routen außer `/login` und `/health`.
- Apple-Stil: eigenes CSS (keine Framework-Abhängigkeit) — `-apple-system/SF`-Fontstack, Cards mit großzügigen Radien, dezente Schatten, SF-Symbols-artige Inline-SVG-Icons; alle Farben als CSS-Variablen. Dark Mode: folgt `prefers-color-scheme`, manueller Toggle in der Navbar überschreibt (localStorage + `data-theme` am Root).
- i18n: alle UI-Strings in `locales/de.json`/`en.json`, Jinja2-Filter `t()`, Sprach-Toggle in der Navbar (Session-gespeichert, Default DE).

### Label-Rendering (`server/app/labels.py`)
- Canvas 306 px breit (29 mm @ 300 dpi), Länge dynamisch (~190 px ≈ 16 mm).
- 2 identische QR-Codes nebeneinander, je ~140 px inkl. Rand; Inhalt `{HOMEBOX_URL}/a/{assetId}` (~30 Zeichen → QR v2–3, ~4 px/Modul, gut scanbar).
- Optional (Checkbox, Default an): Asset-ID (`000-123`) zentriert unter jedem QR.
- Vorschau-Route `GET /label/{assetId}.png` für die Result-Seite.

### Print-Agent (Raspberry Pi)
- `POST /print`: PNG + `copies`, geschützt per statischem `X-Api-Key`; `brother_ql.conversion.convert` (model `QL-500`, label `29`) → Backend `linux_kernel` → `/dev/usb/lp0`.
- `GET /health` für Statusanzeige in Settings. `--dry-run`-Modus schreibt PNG in Datei (Entwicklung ohne Drucker).

### Web-Flow
1. `GET /` — Shop wählen + Bestellnummer eingeben → `POST /fetch` startet Scraper (htmx-Spinner).
2. Edit-Seite: Formular je Artikel (Name, Beschreibung, Menge, Preis, Bestellnr., Shop→`purchaseFrom`), **Lagerort-Dropdown live aus Homebox**, „+ Neuer Lagerort“ (Inline-Feld, htmx-`POST /locations` → in Homebox anlegen, vorauswählen, Formulardaten bleiben erhalten), Homebox-Labels, Checkbox „drucken“.
3. `POST /create` — Items anlegen (POST + PUT Kaufinfos), Result-Seite: Asset-IDs, Label-Vorschau, Druck (Kopienzahl, Asset-ID an/aus).
4. Fehlerpfade: SessionExpired → Link zu Settings; Homebox/Print-Agent offline → klare Meldung, Formulardaten bleiben erhalten.

### Proxmox-Installscript (`install/proxmox-install.sh`)
- Läuft auf dem Proxmox-Host (Muster wie Community-Scripts): Parameter/Abfragen für CTID, Hostname, Storage, Netz (DHCP-Default); lädt Debian-12-Template, `pct create` (empfohlen 2 vCPU / 2 GB RAM / 8 GB Disk wegen Chromium), startet Container, führt per `pct exec` das `install-in-lxc.sh` aus (git clone von GitHub, venv, `playwright install --with-deps chromium`, `.env`-Assistent inkl. Passwort-Hash und SECRET_KEY-Generierung, systemd enable/start), gibt abschließend `http://<ct-ip>:8000` aus.

### Update-Script (`install/update.sh`)
- Einzeiler-Update im Container (`bash update.sh` oder via `pct exec`): `git pull` im App-Verzeichnis, Dependencies nur bei geändertem `pyproject.toml`/Lockfile neu installieren (inkl. Playwright-Browser bei Versionswechsel), `systemctl restart order2homebox`, danach Health-Check + installierte Version/Commit anzeigen. `.env` und `data/` (Cookies) bleiben unangetastet.
- **Raspberry Pi**: `printagent/deploy/install-pi.sh` installiert den Print-Agent komplett (apt-Pakete, git clone, venv, udev-Regel für den QL-500, API-Key-Generierung, systemd enable/start); `update-pi.sh` aktualisiert analog zu `update.sh` (git pull, Deps bei Bedarf, restart, Health-Check).

## Umsetzungsschritte

1. Repo-Grundgerüst: git init, LICENSE (MIT), .gitignore, README-Skelett, `pyproject.toml` ×2.
2. `server`: config, auth (Login-Seite + Session), i18n-Grundgerüst, Basis-Templates mit Apple-CSS + Dark-Mode-Toggle.
3. Homebox-Client (Login/Token, Locations lesen **und anlegen**, Labels, Items) + Tests (respx).
4. Scraper: base (Playwright+Cookies) + 3 Shop-Dateien; Parsing-Tests gegen HTML-Fixtures; cookies.py + Settings-Seite.
5. Label-Renderer + Tests (exakt 306 px, QR per segno rückdekodierbar).
6. Print-Agent inkl. Dry-Run + API-Key; printer.py-Client.
7. Web-Flow komplett (fetch → edit → create → result → print) inkl. Fehlerpfade.
8. Deploy: systemd-Units, udev-Regel, `install/proxmox-install.sh` + `install-in-lxc.sh` + `update.sh` (LXC); `install-pi.sh` + `update-pi.sh` (Raspberry Pi), `install-pi.md`.
9. README (EN) vervollständigen; GitHub-Veröffentlichung: `gh` ist lokal nicht installiert → entweder `winget install GitHub.cli` + `gh auth login`, oder User legt Repo an und nennt die Remote-URL (wird am Ende geklärt).

## Verifikation

- `pytest` in `server/`: Scraper-Parsing (HTML-Fixtures), Label-Maße + QR-Dekodierung, Homebox-Client-Mocks, Auth-Flow.
- Lokal: `uvicorn app.main:app` + Print-Agent im `--dry-run` → kompletten Flow durchklicken (Scraper mit Fixture-Injection statt Live-Fetch), prüfen dass ein korrektes 306-px-PNG mit 2 QR + Asset-ID beim Agent ankommt; Login, Dark-Mode und DE/EN-Umschaltung manuell prüfen.
- Live-Scraping gegen echte Shops + echter Druck via Pi macht der User nach Deployment (Testdruck-Button in Settings; install-Docs enthalten die Schritte). Hinweis im README: Scraper-Selektoren können bei Shop-Updates brechen — deshalb 1 Datei pro Shop.

## Nicht in v1 (bewusst)

- Automatischer Shop-Login (2FA/Captcha), E-Mail-Import, Browser-Extension.
- Produktbilder nach Homebox übertragen (v2-Kandidat: Bild-URL wird bereits gescrapt).
- Druck-Historie/Datenbank, Multi-User.
