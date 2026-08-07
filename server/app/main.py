"""order2homebox web app: fetch order → edit → create Homebox items → print labels."""
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from . import cookies as cookie_store
from . import prefs
from . import printer
from .auth import (
    SESSION_COOKIE,
    LoginRequired,
    create_session_token,
    require_login,
    verify_credentials,
)
from .config import settings
from .homebox import HomeboxClient, HomeboxError
from .i18n import LANG_COOKIE, get_lang, load_translations, t
from .labels import (
    HEIGHT_FORCE,
    HEIGHT_GROW,
    HEIGHT_KEEP,
    HEIGHT_MODES,
    clean_text_line,
    render_label_png,
    render_text_label_png,
)
from .models import Order, OrderItemDraft, Shop
from .scrapers import ParseFailed, ScrapeError, SessionExpired, get_scraper

BASE_DIR = Path(__file__).parent
DOCS_URL = "https://github.com/skyhell/order2homebox#readme"
# Homebox renders an item's asset id as the last three digits of an
# incrementing integer, dash-separated and zero-padded to at least three:
# 1 -> 000-001, 629 -> 000-629, 12345678 -> 12345-678. So the group after the
# dash is always exactly three digits and the one before it never fewer —
# anything else is a typo, not an id Homebox ever handed out.
ASSET_ID_RE = re.compile(r"^[0-9]{3,10}-[0-9]{3}$|^[0-9]{1,10}$")
# Homebox asset deep link, e.g. .../a/000-629. Grabs the whole segment and
# lets ASSET_ID_RE judge it: matching the id shape here instead would trim a
# malformed link down to the part that happens to fit and print that.
ASSET_IN_URL_RE = re.compile(r"/a/([0-9-]+)")
# Item page URL carries the item UUID, e.g. .../item/a23e834c-861a-...
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

logger = logging.getLogger("order2homebox")

homebox = HomeboxClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_translations()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    yield
    await homebox.close()


app = FastAPI(title="order2homebox", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def asset_url(name: str) -> str:
    """Static URL stamped with the file's mtime. Starlette sends no
    Cache-Control for static files, so a browser may keep an old app.js for
    hours after an update — and the app version is no help, fixes ship between
    releases. git rewrites the mtime of every file a pull changes."""
    try:
        stamp = int((BASE_DIR / "static" / name).stat().st_mtime)
    except OSError:
        return f"/static/{name}"
    return f"/static/{name}?v={stamp}"


def _context(request: Request, **context) -> dict:
    lang = get_lang(request)
    context.update(
        request=request,
        lang=lang,
        t=lambda key, **kw: t(key, lang, **kw),
        shops=list(Shop),
        homebox_url=settings.qr_base_url,
        version=__version__,
        docs_url=DOCS_URL,
        show_asset_id_default=settings.label_show_asset_id,
        asset=asset_url,
    )
    return context


def render(request: Request, template: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, template, _context(request, **context))


def render_fragment(request: Request, template: str, **context) -> str:
    """A template rendered to a string instead of a response, so one response
    can carry several fragments (htmx out-of-band swap)."""
    return templates.get_template(template).render(_context(request, **context))


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


# -- auth & misc ------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return render(request, "login.html", error="")


@app.post("/login")
async def login_submit(
    request: Request, username: str = Form(""), password: str = Form("")
):
    if not verify_credentials(username.strip(), password):
        return render(request, "login.html", error=t("login_error", get_lang(request)))
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(username.strip()),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/lang/{lang}")
async def switch_language(lang: str, request: Request):
    response = RedirectResponse(request.headers.get("referer", "/"), status_code=303)
    if lang in ("de", "en"):
        response.set_cookie(LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365)
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


# -- order flow --------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: str = Depends(require_login)):
    return render(request, "index.html", error="")


@app.post("/fetch", response_class=HTMLResponse)
async def fetch_order(
    request: Request,
    shop: Shop = Form(...),
    order_no: str = Form(...),
    user: str = Depends(require_login),
):
    order_no = order_no.strip()
    lang = get_lang(request)
    scraper = get_scraper(shop)
    warning = ""
    try:
        order = await scraper.fetch_order(order_no)
        cookie_store.record_success(shop)
    except SessionExpired:
        return render(
            request,
            "index.html",
            error=t("err_session_expired", lang, shop=shop.display_name),
            error_settings_link=True,
        )
    except ParseFailed:
        # Page loaded but nothing recognized → manual entry with a warning
        order = Order(shop=shop, order_no=order_no, items=[OrderItemDraft()])
        warning = t("err_parse_failed", lang, shop=shop.display_name)
    except ScrapeError as exc:
        return render(request, "index.html", error=str(exc))
    except Exception as exc:  # noqa: BLE001 — never show a bare 500 for a scrape
        logger.exception("scrape failed for %s order %s", shop.value, order_no)
        return render(
            request,
            "index.html",
            error=t("err_scrape_crashed", lang, shop=shop.display_name, error=str(exc)),
        )
    return await _edit_page(request, order, warning=warning)


@app.get("/manual", response_class=HTMLResponse)
async def manual_entry(
    request: Request,
    shop: Shop = Shop.amazon,
    order_no: str = "",
    user: str = Depends(require_login),
):
    order = Order(shop=shop, order_no=order_no, items=[OrderItemDraft()])
    return await _edit_page(request, order)


async def _edit_page(request: Request, order: Order, warning: str = "") -> HTMLResponse:
    lang = get_lang(request)
    try:
        locations = await homebox.get_locations()
        labels = await homebox.get_labels()
    except HomeboxError as exc:
        return render(
            request, "index.html", error=f"{t('err_homebox', lang)}: {exc}"
        )
    return render(
        request,
        "edit.html",
        order=order,
        locations=locations,
        hb_labels=labels,
        warning=warning,
        # Pre-select the location last used, for every item: an order usually
        # goes to one and the same place.
        selected_location_id=prefs.get_last_location_id(),
    )


@app.post("/locations", response_class=HTMLResponse)
async def create_location(
    request: Request,
    name: str = Form(...),
    idx: int = Form(0),
    user: str = Depends(require_login),
):
    """Create a Homebox location inline; returns the refreshed <select> fragment."""
    lang = get_lang(request)
    try:
        created = await homebox.create_location(name.strip())
        locations = await homebox.get_locations()
    except HomeboxError as exc:
        return HTMLResponse(
            f'<p class="error-text">{t("err_homebox", lang)}: {exc}</p>',
            status_code=200,
        )
    return render(
        request,
        "_location_select.html",
        idx=idx,
        locations=locations,
        selected_id=created.get("id", ""),
    )


def _order_from_form(form) -> Order:
    return Order(
        shop=Shop(form.get("shop", "amazon")),
        order_no=str(form.get("order_no", "")).strip(),
        order_date=str(form.get("order_date", "")).strip(),
    )


def _item_from_form(form, i: int):
    """Item fields for index i → (draft, location_id, label_ids, want_print,
    show_id), or None when the card was removed in the UI."""
    if f"item-{i}-name" not in form:
        return None
    try:
        quantity = max(1, int(form.get(f"item-{i}-quantity", 1)))
    except ValueError:
        quantity = 1
    price_raw = str(form.get(f"item-{i}-price", "")).strip().replace(",", ".")
    try:
        unit_price = float(price_raw) if price_raw else None
    except ValueError:
        unit_price = None
    draft = OrderItemDraft(
        name=str(form.get(f"item-{i}-name", "")).strip(),
        description=str(form.get(f"item-{i}-description", "")).strip(),
        quantity=quantity,
        unit_price=unit_price,
        product_url=str(form.get(f"item-{i}-url", "")).strip(),
    )
    location_id = str(form.get(f"item-{i}-location", ""))
    label_ids = [str(v) for v in form.getlist(f"item-{i}-labels")]
    want_print = form.get(f"item-{i}-print") is not None
    show_id = form.get(f"item-{i}-showid") is not None
    return draft, location_id, label_ids, want_print, show_id


async def _create_and_print(
    draft: OrderItemDraft,
    order: Order,
    location_id: str,
    label_ids: list[str],
    want_print: bool,
    show_id: bool,
) -> dict:
    entry = {
        "draft": draft,
        "error": "",
        "item": None,
        "printed": False,
        "print_error": "",
        # Carried into the result card so its checkbox and preview show what
        # was actually printed, not the global default.
        "show_asset_id": show_id,
    }
    try:
        item = await homebox.create_item(draft, order, location_id, label_ids)
        entry["item"] = item
    except HomeboxError as exc:
        entry["error"] = str(exc)
        return entry
    if want_print and item.get("assetId"):
        png = render_label_png(
            item["assetId"],
            homebox.asset_qr_url(item["assetId"]),
            show_asset_id=show_id,
            qr_per_row=settings.label_qr_per_row,
        )
        try:
            await printer.print_png(png, copies=1)
            entry["printed"] = True
        except printer.PrintError as exc:
            entry["print_error"] = str(exc)
    return entry


@app.post("/create", response_class=HTMLResponse)
async def create_items(request: Request, user: str = Depends(require_login)):
    form = await request.form()
    lang = get_lang(request)
    order = _order_from_form(form)

    results = []
    count = int(form.get("item_count", 0))
    for i in range(count):
        parsed = _item_from_form(form, i)
        if parsed is None or not parsed[0].name:
            continue  # card removed or already created via its own button
        draft, location_id, label_ids, want_print, show_id = parsed
        entry = await _create_and_print(
            draft, order, location_id, label_ids, want_print, show_id
        )
        if entry["item"]:
            prefs.set_last_location_id(location_id)
        results.append(entry)

    if not results:
        return render(request, "index.html", error=t("err_nothing_created", lang))
    return render(
        request,
        "result.html",
        order=order,
        results=results,
    )


@app.post("/create-item", response_class=HTMLResponse)
async def create_single_item(request: Request, user: str = Depends(require_login)):
    """Per-item button on the edit page: create just this item in Homebox
    (+ print its label) and swap the card for a result fragment."""
    form = await request.form()
    lang = get_lang(request)
    idx = int(form.get("idx", 0))
    order = _order_from_form(form)
    parsed = _item_from_form(form, idx)
    if parsed is None:
        return HTMLResponse(status_code=400)
    draft, location_id, label_ids, want_print, show_id = parsed

    async def card_with_error(message: str) -> HTMLResponse:
        try:
            locations = await homebox.get_locations()
            labels = await homebox.get_labels()
        except HomeboxError:
            locations, labels = [], []
        return render(
            request,
            "_item_card.html",
            item=draft,
            idx=idx,
            card_error=message,
            locations=locations,
            hb_labels=labels,
            selected_location_id=location_id,
            selected_label_ids=label_ids,
            want_print=want_print,
            want_show_id=show_id,
        )

    if not draft.name:
        return await card_with_error(t("err_name_required", lang))

    entry = await _create_and_print(
        draft, order, location_id, label_ids, want_print, show_id
    )
    if entry["error"]:
        return await card_with_error(f"{t('err_homebox', lang)}: {entry['error']}")
    prefs.set_last_location_id(location_id)
    return render(request, "_item_result.html", r=entry, idx=idx)


# -- print from a Homebox link ------------------------------------------------


class LabelRefError(Exception):
    """Input could not be resolved to an asset ID; ``key`` is a locale key."""

    def __init__(self, key: str):
        self.key = key
        super().__init__(key)


async def resolve_asset_id(raw: str) -> str:
    """Turn a pasted Homebox link or asset ID into a Homebox asset ID.

    Accepts an ``/a/{assetId}`` deep link, an ``/item/{uuid}`` page URL (looked
    up via the API), or a bare asset ID like ``000-629``. Raises
    ``LabelRefError`` (locale key) or ``HomeboxError`` on failure."""
    raw = (raw or "").strip()
    if not raw:
        raise LabelRefError("err_label_empty")
    in_url = ASSET_IN_URL_RE.search(raw)
    if in_url:
        if not ASSET_ID_RE.match(in_url.group(1)):
            raise LabelRefError("err_label_unrecognized")
        return in_url.group(1)
    uuid = UUID_RE.search(raw)
    if uuid:
        item = await homebox.get_item(uuid.group(0))
        asset = str(item.get("assetId") or "").strip()
        if not asset or asset in ("0", "000-000") or not ASSET_ID_RE.match(asset):
            raise LabelRefError("err_label_no_asset")
        return asset
    if ASSET_ID_RE.match(raw):
        return raw
    raise LabelRefError("err_label_unrecognized")


@app.get("/label", response_class=HTMLResponse)
async def label_tool(request: Request, user: str = Depends(require_login)):
    return render(request, "label.html")


@app.post("/label/resolve", response_class=HTMLResponse)
async def label_resolve(
    request: Request, link: str = Form(""), user: str = Depends(require_login)
):
    lang = get_lang(request)
    try:
        asset_id = await resolve_asset_id(link)
    except LabelRefError as exc:
        return HTMLResponse(
            f'<div class="banner banner-error">{t(exc.key, lang)}</div>'
        )
    except HomeboxError as exc:
        return HTMLResponse(
            f'<div class="banner banner-error">{t("err_homebox", lang)}: {exc}</div>'
        )
    return render(request, "_label_result.html", asset_id=asset_id)


# -- text-only labels ---------------------------------------------------------


def _text_lines(line1: str, line2: str) -> list[str]:
    return [line for line in (clean_text_line(line1), clean_text_line(line2)) if line]


@app.get("/text", response_class=HTMLResponse)
async def text_label_tool(request: Request, user: str = Depends(require_login)):
    return render(
        request,
        "text_label.html",
        history=prefs.get_text_labels(),
        height_mode=prefs.get_text_height_mode(),
        HEIGHT_KEEP=HEIGHT_KEEP,
        HEIGHT_FORCE=HEIGHT_FORCE,
    )


@app.get("/text.png")
async def text_label_preview(
    line1: str = "",
    line2: str = "",
    height: str = HEIGHT_GROW,
    user: str = Depends(require_login),
):
    """Preview of the text label, refreshed while typing. The text is in the
    URL, so an unchanged text is served from the browser cache."""
    lines = _text_lines(line1, line2)
    if not lines:
        return Response(status_code=404)
    png = render_text_label_png(lines, height_mode=height)
    return Response(content=png, media_type="image/png")


@app.post("/text/print", response_class=HTMLResponse)
async def print_text_label(
    request: Request,
    line1: str = Form(""),
    line2: str = Form(""),
    copies: int = Form(1),
    height_mode: str = Form(HEIGHT_GROW),
    user: str = Depends(require_login),
):
    lang = get_lang(request)
    lines = _text_lines(line1, line2)
    if not lines:
        return HTMLResponse(
            f'<span class="print-status error-text">{t("err_text_empty", lang)}</span>'
        )
    if height_mode not in HEIGHT_MODES:
        height_mode = HEIGHT_GROW
    png = render_text_label_png(lines, height_mode=height_mode)
    try:
        await printer.print_png(png, copies=max(1, min(copies, 20)))
    except printer.PrintError as exc:
        return HTMLResponse(
            f'<span class="print-status error-text">{t("print_failed", lang)}: {exc}</span>'
        )
    # Only a label that really came out is worth remembering — same rule as the
    # last-used location. The list is swapped out of band because this response
    # already targets the status line.
    prefs.remember_text_label(lines, height_mode)
    history = render_fragment(
        request, "_text_history.html", history=prefs.get_text_labels(), oob=True
    )
    return HTMLResponse(
        f'<span class="print-status ok-text">{t("print_ok", lang)}</span>{history}'
    )


# -- labels & printing --------------------------------------------------------


@app.get("/label/{asset_id}.png")
async def label_preview(
    asset_id: str,
    text: int = 1,
    count: int = 0,
    user: str = Depends(require_login),
):
    if not ASSET_ID_RE.match(asset_id):
        return Response(status_code=404)
    png = render_label_png(
        asset_id,
        homebox.asset_qr_url(asset_id),
        show_asset_id=bool(text),
        qr_per_row=count or settings.label_qr_per_row,
    )
    return Response(content=png, media_type="image/png")


@app.post("/print", response_class=HTMLResponse)
async def print_label(
    request: Request,
    asset_id: str = Form(...),
    copies: int = Form(1),
    show_text: bool = Form(False),
    user: str = Depends(require_login),
):
    lang = get_lang(request)
    if not ASSET_ID_RE.match(asset_id):
        return HTMLResponse(f'<span class="print-status error-text">?</span>')
    png = render_label_png(
        asset_id,
        homebox.asset_qr_url(asset_id),
        show_asset_id=show_text,
        qr_per_row=settings.label_qr_per_row,
    )
    try:
        await printer.print_png(png, copies=max(1, min(copies, 20)))
    except printer.PrintError as exc:
        return HTMLResponse(
            f'<span class="print-status error-text">{t("print_failed", lang)}: {exc}</span>'
        )
    return HTMLResponse(
        f'<span class="print-status ok-text">{t("print_ok", lang)}</span>'
    )


# -- settings -----------------------------------------------------------------


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request, msg: str = "", user: str = Depends(require_login)
):
    lang = get_lang(request)
    shop_status = {shop: cookie_store.cookie_status(shop) for shop in Shop}
    # Neither connection is checked here: an unreachable host answers with
    # nothing at all, so the check sits out its full timeout before any HTML is
    # sent. Both rows fetch their own state once the page has loaded.
    return render(
        request,
        "settings.html",
        shop_status=shop_status,
        msg=t(msg, lang) if msg else "",
    )


@app.get("/settings/homebox-status", response_class=HTMLResponse)
async def homebox_status_fragment(request: Request, user: str = Depends(require_login)):
    try:
        await homebox.status()
        hb_status = {"ok": True, "url": settings.homebox_url}
    except HomeboxError as exc:
        hb_status = {"ok": False, "error": str(exc), "url": settings.homebox_url}
    response = render(request, "_homebox_status.html", hb_status=hb_status)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/settings/cookies/{shop}")
async def import_cookies(
    shop: Shop, cookies_json: str = Form(...), user: str = Depends(require_login)
):
    try:
        cookie_store.save_cookies(shop, cookies_json)
    except cookie_store.CookieError:
        return RedirectResponse("/settings?msg=cookies_invalid", status_code=303)
    return RedirectResponse("/settings?msg=cookies_saved", status_code=303)


@app.get("/settings/agent-status", response_class=HTMLResponse)
async def agent_status_fragment(request: Request, user: str = Depends(require_login)):
    """Polled by the settings page so the print-agent row goes green again by
    itself after the Pi was shut down from here and switched back on."""
    response = render(request, "_agent_status.html", agent_status=await printer.health())
    # A poll served from the browser cache would show a state that is minutes
    # old — the one thing this endpoint must never do.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/settings/test-print")
async def test_print(request: Request, user: str = Depends(require_login)):
    lang = get_lang(request)
    png = render_label_png(
        "000-000",
        homebox.asset_qr_url("000-000"),
        show_asset_id=True,
        qr_per_row=settings.label_qr_per_row,
    )
    try:
        await printer.print_png(png, copies=1)
    except printer.PrintError as exc:
        return HTMLResponse(
            f'<span class="print-status error-text">{t("print_failed", lang)}: {exc}</span>'
        )
    return HTMLResponse(
        f'<span class="print-status ok-text">{t("test_print_sent", lang)}</span>'
    )


@app.post("/settings/shutdown-agent")
async def shutdown_agent(request: Request, user: str = Depends(require_login)):
    """Power the Raspberry Pi down cleanly (it is headless)."""
    lang = get_lang(request)
    try:
        await printer.shutdown()
    except printer.PrintError as exc:
        return HTMLResponse(
            f'<span class="print-status error-text">{t("shutdown_failed", lang)}: {exc}</span>'
        )
    return HTMLResponse(
        f'<span class="print-status ok-text">{t("shutdown_sent", lang)}</span>'
    )
