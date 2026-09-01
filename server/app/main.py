"""order2homebox web app: fetch order → edit → create Homebox items → print labels."""
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from . import agents
from . import cookies as cookie_store
from . import draft
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
    clamp_qr_per_row,
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


def _print_error_parts(message: str, lang: str) -> tuple[str, str]:
    """The red line about a failed print, and the technical detail behind it.

    A cause we recognise gets a sentence of its own and the agent's wording moves
    to the back, small; anything else stays verbatim up front, where it is the
    only clue there is.
    """
    key = printer.error_key(message)
    if not key:
        return f"{t('print_failed', lang)}: {message}", ""
    # Nothing technical happened here — no agent was asked — so there is no
    # wording of one to keep.
    detail = "" if message == printer.NO_PRINTER else message
    return f"{t('print_failed', lang)}: {t(key, lang)}", detail


def _print_status(request: Request, ok: bool, message: str, detail: str = "") -> str:
    """The status fragment every print route answers with — the same markup the
    result card is rendered with, so a swap cannot change how it looks."""
    status = {"ok": ok, "message": message, "detail": detail}
    return render_fragment(request, "_print_status.html", status=status)


def _card_print_status(entry: dict, lang: str) -> dict | None:
    """What a result card says about printing, in the shape the print routes
    swap in — None while nothing was tried, so the box stays empty."""
    if entry.get("printed"):
        return {"ok": True, "message": f"{t('printed_auto', lang)} ✓", "detail": ""}
    if entry.get("print_error"):
        message, detail = _print_error_parts(entry["print_error"], lang)
        return {"ok": False, "message": message, "detail": detail}
    return None


def _context(request: Request, **context) -> dict:
    lang = get_lang(request)
    # The edit page knows it is the draft before the browser has saved it (a
    # fetch clears the file first), so it may state its own card count.
    draft_info = context.pop("draft_info", None) or draft.summary()
    printers = agents.load()
    context.update(
        request=request,
        lang=lang,
        t=lambda key, **kw: t(key, lang, **kw),
        card_print_status=lambda entry: _card_print_status(entry, lang),
        shops=list(Shop),
        homebox_url=settings.qr_base_url,
        version=__version__,
        docs_url=DOCS_URL,
        show_asset_id_default=settings.label_show_asset_id,
        # Copy counts are a habit, and a different one per kind of label.
        copies_default=prefs.get_last_copies("label"),
        text_copies_default=prefs.get_last_copies("text"),
        asset=asset_url,
        # The nav link back to a half-finished order; None while there is none.
        draft_info=draft_info,
        # Which Pi this browser prints on, and how many there are to choose
        # from — with a single printer the pages say nothing about it at all.
        printers=printers,
        print_target=agents.selected(request, printers),
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


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Every browser asks the root for this on its own, whatever the <head>
    says — and a page without one (an opened label PNG, for instance) has no
    <head> at all. No login: it is an icon."""
    return FileResponse(BASE_DIR / "static" / "favicon.ico")


# -- order flow --------------------------------------------------------------


def _order_history() -> list[dict]:
    """Remembered order numbers as history entries, the shop as the note. A
    shop that no longer exists is skipped rather than breaking the page."""
    entries = []
    for entry in prefs.get_orders():
        try:
            shop = Shop(entry["shop"])
        except ValueError:
            continue
        entries.append(
            {"value": entry["order_no"], "note": shop.display_name, "shop": shop.value}
        )
    return entries


def _render_index(request: Request, **context) -> HTMLResponse:
    """The start page, which four paths render — the history belongs on all of
    them, an error above the form least of all a reason to lose it."""
    return render(
        request,
        "index.html",
        order_history=_order_history(),
        # Most orders come from the shop the last one came from.
        selected_shop=prefs.get_last_shop(),
        **context,
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: str = Depends(require_login)):
    return _render_index(request, error="")


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
    # Remembered before the attempt, not after it: expired cookies or a changed
    # shop page are exactly when the same number is needed again right away.
    prefs.remember_order(shop.value, order_no)
    try:
        order = await scraper.fetch_order(order_no)
        cookie_store.record_success(shop)
    except SessionExpired:
        return _render_index(
            request,
            error=t("err_session_expired", lang, shop=shop.display_name),
            error_settings_link=True,
        )
    except ParseFailed:
        # Page loaded but nothing recognized → manual entry with a warning
        order = Order(shop=shop, order_no=order_no, items=[OrderItemDraft()])
        warning = t("err_parse_failed", lang, shop=shop.display_name)
    except ScrapeError as exc:
        return _render_index(request, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — never show a bare 500 for a scrape
        logger.exception("scrape failed for %s order %s", shop.value, order_no)
        return _render_index(
            request,
            error=t("err_scrape_crashed", lang, shop=shop.display_name, error=str(exc)),
        )
    # This is the "next fetch" that replaces what was on the page; the browser
    # writes the new state back as soon as the page has loaded.
    draft.clear()
    return await _edit_page(request, order, warning=warning)


@app.get("/manual", response_class=HTMLResponse)
async def manual_entry(
    request: Request,
    shop: Shop = Shop.amazon,
    order_no: str = "",
    user: str = Depends(require_login),
):
    order = Order(shop=shop, order_no=order_no, items=[OrderItemDraft()])
    draft.clear()  # starting by hand replaces the page just like a fetch does
    return await _edit_page(request, order)


@app.post("/draft")
async def save_draft(request: Request, user: str = Depends(require_login)):
    """The edit form as it currently stands, sent while typing and once more
    when the page goes away — so switching pages does not lose it."""
    draft.save(await request.form())
    return Response(status_code=204)


@app.get("/edit", response_class=HTMLResponse)
async def edit_draft(request: Request, user: str = Depends(require_login)):
    """Back to the order being edited. Rebuilt from the stored form with the
    very function that reads the real one, so both stay in step."""
    data = draft.load()
    if not data:
        return RedirectResponse("/", status_code=303)
    stored = draft.form(data)
    order = _order_from_form(stored)
    created = draft.created_items(data)
    cards = []
    for idx in range(int(data.get("item_count", 0))):
        entry = created.get(str(idx))
        if entry:
            cards.append({"idx": idx, "result": _result_from_created(entry)})
            continue
        parsed = _item_from_form(stored, idx)
        if parsed is None:
            continue  # the card was removed before leaving the page
        item, location_id, label_ids, want_print, _, want_show_id, qr_per_row = parsed
        cards.append({
            "idx": idx,
            "item": item,
            "location_id": location_id,
            "label_ids": label_ids,
            "want_print": want_print,
            # The answer, not the label: at three per row the card shows the box
            # off and disabled, but unticking three-up has to give this back.
            "want_show_id": want_show_id,
            "want_qr3": qr_per_row == 3,
            "result": None,
        })
    return await _edit_page(
        request, order, cards=cards, item_count=int(data.get("item_count", 0))
    )


def _result_from_created(entry: dict) -> dict:
    """A stored created item in the shape _item_result.html reads."""
    return {
        "draft": OrderItemDraft(name=entry.get("name", "")),
        "item": {"assetId": entry.get("asset_id", ""), "id": entry.get("item_id", "")},
        "printed": entry.get("printed", False),
        "print_error": entry.get("print_error", ""),
        "error": "",
        "show_asset_id": entry.get("show_asset_id", False),
        # Drafts written before the answer was kept apart from the label only
        # have the label; then they are the same thing.
        "want_asset_id": entry.get("want_asset_id", entry.get("show_asset_id", False)),
        "qr_per_row": entry.get("qr_per_row", 2),
    }


def _fresh_cards(order: Order) -> list[dict]:
    """One card view per scraped item, all with the same defaults."""
    return [
        {
            "idx": idx,
            "item": item,
            # Pre-select the location last used, for every item: an order
            # usually goes to one and the same place.
            "location_id": prefs.get_last_location_id(),
            "label_ids": [],
            "want_print": True,
            "want_show_id": settings.label_show_asset_id,
            # Clamped like everywhere else: .env is not validated, and an
            # untrimmed 4 would fail the "is this three?" test and start the
            # card at two codes, where a request for 4 draws three.
            "want_qr3": clamp_qr_per_row(settings.label_qr_per_row) == 3,
            "result": None,
        }
        for idx, item in enumerate(order.items)
    ]


async def _edit_page(
    request: Request,
    order: Order,
    warning: str = "",
    cards: list[dict] | None = None,
    item_count: int | None = None,
) -> HTMLResponse:
    """The edit page. `cards` carries per-card state (a restored draft has a
    different location and different checkboxes per card, and some cards are
    results rather than inputs); without it every item starts from the
    defaults."""
    lang = get_lang(request)
    try:
        locations = await homebox.get_locations()
        labels = await homebox.get_labels()
    except HomeboxError as exc:
        return _render_index(request, error=f"{t('err_homebox', lang)}: {exc}")
    if cards is None:
        cards = _fresh_cards(order)
    return render(
        request,
        "edit.html",
        order=order,
        cards=cards,
        # A removed card must not shift the indexes of the others, so the count
        # keeps the gap — /create skips indexes that are not submitted.
        item_count=item_count if item_count is not None else len(cards),
        locations=locations,
        hb_labels=labels,
        warning=warning,
        draft_info={"order_no": order.order_no, "shop": order.shop.value,
                    "cards": len(cards)},
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


def _qr_per_row_off() -> int:
    """How many codes an unticked three-up box asks for: the configured count —
    unless that is three, because then the box could never be turned off and
    two codes are what "not three small ones" leaves. One definition, so the
    preview, the item card and /print cannot drift apart on it.

    The count is clamped first: .env is not validated, and a 4 that only the
    renderer trims would slip past the "three per row" test below."""
    configured = clamp_qr_per_row(settings.label_qr_per_row)
    return configured if configured != 3 else 2


def _item_from_form(form, i: int):
    """Item fields for index i → (item_draft, location_id, label_ids,
    want_print, show_id, want_show_id, qr_per_row), or None when the card was
    removed in the UI. `form` is either a real FormData or a draft.StoredForm.

    `show_id` is what goes on the label, `want_show_id` what was asked for —
    they differ only at three codes per row, where the id has no room."""
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
    item_draft = OrderItemDraft(
        # The name field is a textarea, so line breaks can be typed or pasted
        # into it — a Homebox item title is one line.
        name=" ".join(str(form.get(f"item-{i}-name", "")).split()),
        description=str(form.get(f"item-{i}-description", "")).strip(),
        quantity=quantity,
        unit_price=unit_price,
        product_url=str(form.get(f"item-{i}-url", "")).strip(),
    )
    location_id = str(form.get(f"item-{i}-location", ""))
    label_ids = [str(v) for v in form.getlist(f"item-{i}-labels")]
    want_print = form.get(f"item-{i}-print") is not None
    show_id = form.get(f"item-{i}-showid") is not None
    want_show_id = show_id
    # Three small codes for a small part you have several of. The id has no
    # room next to them, so it is not printed regardless of the checkbox.
    qr_per_row = 3 if form.get(f"item-{i}-qr3") is not None else _qr_per_row_off()
    if qr_per_row == 3:
        # The box is disabled here, and a disabled checkbox is not submitted at
        # all — so what it would have said rides along in a hidden field beside
        # it. Remembered, not obeyed: ticking three per row hides the choice,
        # it does not answer it, and the card has to give it back when the box
        # is unticked again. Only read where the checkbox is silenced, so a
        # page whose JS never ran is still taken at its checkbox.
        want_show_id = str(form.get(f"item-{i}-showid-want", "")) == "1"
        show_id = False
    return (
        item_draft, location_id, label_ids, want_print, show_id, want_show_id, qr_per_row
    )


async def _create_and_print(
    item_draft: OrderItemDraft,
    order: Order,
    location_id: str,
    label_ids: list[str],
    want_print: bool,
    show_id: bool,
    qr_per_row: int = 0,
    agent: agents.Agent | None = None,
    want_show_id: bool | None = None,
) -> dict:
    qr_per_row = qr_per_row or _qr_per_row_off()
    entry = {
        "draft": item_draft,
        "error": "",
        "item": None,
        "printed": False,
        "print_error": "",
        # Carried into the result card so its checkbox and preview show what
        # was actually printed, not the global default.
        "show_asset_id": show_id,
        # And what was asked for, which is the same thing except at three per
        # row: the card offers a reprint, and a choice the id had no room for
        # has to come back when the count does.
        "want_asset_id": show_id if want_show_id is None else want_show_id,
        "qr_per_row": qr_per_row,
    }
    try:
        item = await homebox.create_item(item_draft, order, location_id, label_ids)
        entry["item"] = item
        # Both create routes come through here, so the reprint page learns about
        # every new asset ID — printing it is not the point, having it is: an
        # item created without a label is precisely what gets reprinted later.
        prefs.remember_asset_ref(str(item.get("assetId") or ""), item_draft.name)
    except HomeboxError as exc:
        entry["error"] = str(exc)
        return entry
    if want_print and item.get("assetId"):
        png = render_label_png(
            item["assetId"],
            homebox.asset_qr_url(item["assetId"]),
            show_asset_id=show_id,
            qr_per_row=qr_per_row,
        )
        if agent is None:
            # Nothing to print on: the item is created either way, and the card
            # says why no label came out.
            entry["print_error"] = printer.NO_PRINTER
            return entry
        try:
            await printer.print_png(agent, png, copies=1)
            entry["printed"] = True
        except printer.PrintError as exc:
            entry["print_error"] = str(exc)
    return entry


@app.post("/create", response_class=HTMLResponse)
async def create_items(request: Request, user: str = Depends(require_login)):
    form = await request.form()
    lang = get_lang(request)
    order = _order_from_form(form)

    draft.save(form)
    results = []
    count = int(form.get("item_count", 0))
    # One printer for the whole order: it is the one this browser chose, and
    # picking it per item would only mean reading the file again.
    agent = agents.selected(request)
    for i in range(count):
        parsed = _item_from_form(form, i)
        if parsed is None or not parsed[0].name:
            continue  # card removed or already created via its own button
        item_draft, location_id, label_ids, want_print, show_id, want_show_id, qr_per_row = (
            parsed
        )
        entry = await _create_and_print(
            item_draft, order, location_id, label_ids, want_print, show_id, qr_per_row,
            agent=agent, want_show_id=want_show_id,
        )
        # The card's index in the form, not its place in this list: skipped
        # cards make the two drift apart, and the draft is keyed by the former.
        # The print button on the result page sends this number back.
        entry["card_idx"] = i
        if entry["item"]:
            prefs.set_last_location_id(location_id)
            draft.mark_created(i, entry)
        results.append(entry)

    if not results:
        return _render_index(request, error=t("err_nothing_created", lang))
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
    item_draft, location_id, label_ids, want_print, show_id, want_show_id, qr_per_row = (
        parsed
    )

    async def card_with_error(message: str) -> HTMLResponse:
        try:
            locations = await homebox.get_locations()
            labels = await homebox.get_labels()
        except HomeboxError:
            locations, labels = [], []
        return render(
            request,
            "_item_card.html",
            item=item_draft,
            idx=idx,
            card_error=message,
            locations=locations,
            hb_labels=labels,
            selected_location_id=location_id,
            selected_label_ids=label_ids,
            want_print=want_print,
            want_show_id=want_show_id,
            want_qr3=qr_per_row == 3,
        )

    if not item_draft.name:
        return await card_with_error(t("err_name_required", lang))

    entry = await _create_and_print(
        item_draft, order, location_id, label_ids, want_print, show_id, qr_per_row,
        agent=agents.selected(request), want_show_id=want_show_id,
    )
    if entry["error"]:
        return await card_with_error(f"{t('err_homebox', lang)}: {entry['error']}")
    prefs.set_last_location_id(location_id)
    # The card is a result card from now on; coming back to the page must not
    # offer it as an input again, or the item gets created twice.
    draft.save(form)
    draft.mark_created(idx, entry)
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


def _asset_history() -> list[dict]:
    """Already history entries — value plus the item name as the note."""
    return prefs.get_asset_refs()


@app.get("/label", response_class=HTMLResponse)
async def label_tool(request: Request, user: str = Depends(require_login)):
    return render(request, "label.html", asset_history=_asset_history())


@app.post("/label/resolve", response_class=HTMLResponse)
async def label_resolve(
    request: Request, link: str = Form(""), user: str = Depends(require_login)
):
    lang = get_lang(request)
    try:
        asset_id = await resolve_asset_id(link)
    except LabelRefError as exc:
        # Nothing to remember: the app just said this is not a label reference,
        # and a typo in the list would only be in the way.
        return HTMLResponse(
            f'<div class="banner banner-error">{t(exc.key, lang)}</div>'
        )
    except HomeboxError as exc:
        # The input was usable, only Homebox did not answer — so it is worth
        # offering again, as typed, since there is no resolved ID yet.
        prefs.remember_asset_ref(link.strip())
        banner = f'<div class="banner banner-error">{t("err_homebox", lang)}: {exc}</div>'
        return HTMLResponse(banner + _asset_history_fragment(request))
    # The resolved ID, not what was pasted: short, unambiguous, resolves again
    # in one step. The refreshed list rides along out of band, because this
    # response targets the result.
    prefs.remember_asset_ref(asset_id)
    result = render_fragment(request, "_label_result.html", asset_id=asset_id)
    return HTMLResponse(result + _asset_history_fragment(request))


def _asset_history_fragment(request: Request) -> str:
    return render_fragment(
        request,
        "_field_history.html",
        field="label-link",
        input_id="label-link",
        entries=_asset_history(),
        oob=True,
    )


# -- text-only labels ---------------------------------------------------------


def _text_lines(line1: str, line2: str) -> list[str]:
    return [line for line in (clean_text_line(line1), clean_text_line(line2)) if line]


def _text_line_history(index: int) -> list[dict]:
    return [{"value": line} for line in prefs.get_text_line_history(index)]


@app.get("/text", response_class=HTMLResponse)
async def text_label_tool(request: Request, user: str = Depends(require_login)):
    return render(
        request,
        "text_label.html",
        history=prefs.get_text_labels(),
        line1_history=_text_line_history(0),
        line2_history=_text_line_history(1),
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
            _print_status(request, ok=False, message=t("err_text_empty", lang))
        )
    if height_mode not in HEIGHT_MODES:
        height_mode = HEIGHT_GROW
    png = render_text_label_png(lines, height_mode=height_mode)
    # Remembered before the print, not after: a label the printer refused is
    # the one that gets typed again. The lists ride back out of band either
    # way, because this response targets the status line.
    prefs.remember_text_label(lines, height_mode)
    prefs.set_last_copies("text", copies)
    history = render_fragment(
        request, "_text_history.html", history=prefs.get_text_labels(), oob=True
    )
    for index in (0, 1):
        history += render_fragment(
            request,
            "_field_history.html",
            field=f"text-line{index + 1}",
            input_id=f"text-line{index + 1}",
            entries=_text_line_history(index),
            oob=True,
        )
    agent = agents.selected(request)
    if agent is None:
        message, detail = _print_error_parts(printer.NO_PRINTER, lang)
        status = _print_status(request, ok=False, message=message, detail=detail)
        return HTMLResponse(f"{status}{history}")
    try:
        await printer.print_png(agent, png, copies=max(1, min(copies, 20)))
    except printer.PrintError as exc:
        message, detail = _print_error_parts(str(exc), lang)
        status = _print_status(request, ok=False, message=message, detail=detail)
        return HTMLResponse(f"{status}{history}")
    status = _print_status(request, ok=True, message=t("print_ok", lang))
    return HTMLResponse(f"{status}{history}")


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
    # Clamped here, not only in the renderer: the ID rule below reads it.
    qr_per_row = clamp_qr_per_row(count) if count else _qr_per_row_off()
    png = render_label_png(
        asset_id,
        homebox.asset_qr_url(asset_id),
        # The same rule /print applies: at three per row the id has no room, so
        # a preview that showed one would promise a label nothing can print.
        show_asset_id=bool(text) and qr_per_row != 3,
        qr_per_row=qr_per_row,
    )
    return Response(content=png, media_type="image/png")


@app.post("/print", response_class=HTMLResponse)
async def print_label(
    request: Request,
    asset_id: str = Form(...),
    copies: int = Form(1),
    show_text: bool = Form(False),
    qr_per_row: int = Form(0),
    # Only the result cards of the edit page send one: they are the only labels
    # whose print result is remembered anywhere.
    card_idx: int = Form(-1),
    user: str = Depends(require_login),
):
    lang = get_lang(request)
    if not ASSET_ID_RE.match(asset_id):
        return HTMLResponse(_print_status(request, ok=False, message="?"))
    qr_per_row = clamp_qr_per_row(qr_per_row) if qr_per_row else _qr_per_row_off()
    # Reprinting must not put the id back where there is no room for it.
    show_id = show_text and qr_per_row != 3
    prefs.set_last_copies("label", copies)
    # Before the attempt, not after it: a label the agent refused is exactly the
    # one that gets printed again in a moment. The name, if this ID has one, is
    # already stored from when the item was created.
    prefs.remember_asset_ref(asset_id)
    png = render_label_png(
        asset_id,
        homebox.asset_qr_url(asset_id),
        show_asset_id=show_id,
        qr_per_row=qr_per_row,
    )
    agent = agents.selected(request)
    if agent is None:
        # Also an attempt, and the card has to remember it: leaving the draft
        # untouched would bring an earlier "printed ✓" back on the next reload,
        # next to the boxes this attempt has already changed. The item cards
        # record the same condition the same way (see _create_and_print).
        draft.update_print_result(
            card_idx, asset_id, printed=False, error=printer.NO_PRINTER,
            show_asset_id=show_id, want_asset_id=show_text, qr_per_row=qr_per_row,
        )
        message, detail = _print_error_parts(printer.NO_PRINTER, lang)
        return HTMLResponse(_print_status(request, ok=False, message=message, detail=detail))
    try:
        await printer.print_png(agent, png, copies=max(1, min(copies, 20)))
    except printer.PrintError as exc:
        # The card is told about this attempt, not only the one at creation time:
        # what the page shows must survive a reload either way.
        draft.update_print_result(
            card_idx, asset_id, printed=False, error=str(exc),
            show_asset_id=show_id, want_asset_id=show_text, qr_per_row=qr_per_row,
        )
        message, detail = _print_error_parts(str(exc), lang)
        return HTMLResponse(_print_status(request, ok=False, message=message, detail=detail))
    draft.update_print_result(
        card_idx, asset_id, printed=True, error="",
        show_asset_id=show_id, want_asset_id=show_text, qr_per_row=qr_per_row,
    )
    return HTMLResponse(_print_status(request, ok=True, message=t("print_ok", lang)))


# -- shop sessions -------------------------------------------------------------


def _msg_is_error(key: str) -> bool:
    """Whether a message named in a redirect is bad news — a refusal must not
    arrive in the same calm blue as "saved"."""
    return key.startswith("err_") or key.endswith("_invalid")


@app.get("/cookies", response_class=HTMLResponse)
async def cookies_page(
    request: Request, msg: str = "", user: str = Depends(require_login)
):
    """The shop sessions have a page of their own: they are what an order fetch
    runs on and need refreshing every few weeks, while the settings next door
    are set up once."""
    lang = get_lang(request)
    return render(
        request,
        "cookies.html",
        shop_status={shop: cookie_store.cookie_status(shop) for shop in Shop},
        msg=t(msg, lang) if msg else "",
        msg_error=_msg_is_error(msg),
    )


# -- settings -----------------------------------------------------------------


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request, msg: str = "", user: str = Depends(require_login)
):
    lang = get_lang(request)
    # Neither connection is checked here: an unreachable host answers with
    # nothing at all, so the check sits out its full timeout before any HTML is
    # sent. Both rows fetch their own state once the page has loaded.
    return render(
        request,
        "settings.html",
        msg=t(msg, lang) if msg else "",
        # A refused printer address is not news, it is a problem — the banner
        # says so in the colour it is read in.
        msg_error=_msg_is_error(msg),
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


@app.post("/cookies/{shop}")
async def import_cookies(
    shop: Shop, cookies_json: str = Form(...), user: str = Depends(require_login)
):
    try:
        cookie_store.save_cookies(shop, cookies_json)
    except cookie_store.CookieError:
        return RedirectResponse("/cookies?msg=cookies_invalid", status_code=303)
    return RedirectResponse("/cookies?msg=cookies_saved", status_code=303)


@app.get("/settings/printers/{agent_id}/status", response_class=HTMLResponse)
async def agent_status_fragment(
    agent_id: str, request: Request, user: str = Depends(require_login)
):
    """Polled by the settings page so a printer row goes green again by itself
    after its Pi was shut down from here and switched back on."""
    agent = agents.by_id(agent_id)
    if agent is None:
        # Removed in another tab: an empty row is the truth, and it stops the
        # poll from asking after a printer that is gone.
        return HTMLResponse("")
    response = render(
        request, "_printer_status.html", agent=agent, agent_status=await printer.health(agent)
    )
    # A poll served from the browser cache would show a state that is minutes
    # old — the one thing this endpoint must never do.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/settings/printers")
async def save_printer(
    request: Request,
    agent_id: str = Form(""),
    name: str = Form(""),
    url: str = Form(""),
    api_key: str = Form(""),
    user: str = Depends(require_login),
):
    """Add a printer, or change one. Blank key on a change keeps the stored one."""
    try:
        agents.save(agent_id, name, url, api_key)
    except agents.AgentError as exc:
        return RedirectResponse(f"/settings?msg={exc}", status_code=303)
    return RedirectResponse("/settings?msg=printer_saved", status_code=303)


@app.post("/settings/printers/{agent_id}/select")
async def select_printer(
    agent_id: str, request: Request, user: str = Depends(require_login)
):
    """Print on this one from now on — on this computer. Another desk keeps its
    own choice, which is the whole point of putting it in a cookie."""
    response = RedirectResponse("/settings", status_code=303)
    if agents.by_id(agent_id):
        response.set_cookie(
            agents.PRINTER_COOKIE,
            agent_id,
            max_age=agents.COOKIE_MAX_AGE,
            samesite="lax",
        )
    return response


@app.post("/settings/printers/{agent_id}/remove")
async def remove_printer(
    agent_id: str, request: Request, user: str = Depends(require_login)
):
    try:
        agents.remove(agent_id)
    except agents.AgentError as exc:
        return RedirectResponse(f"/settings?msg={exc}", status_code=303)
    return RedirectResponse("/settings?msg=printer_removed", status_code=303)


@app.post("/settings/printers/{agent_id}/test-print")
async def test_print(agent_id: str, request: Request, user: str = Depends(require_login)):
    lang = get_lang(request)
    agent = agents.by_id(agent_id)
    if agent is None:
        return HTMLResponse(_print_status(request, ok=False, message=t("err_no_printer", lang)))
    png = render_label_png(
        "000-000",
        homebox.asset_qr_url("000-000"),
        # The layout an unticked three-up box asks for, like everywhere else: a
        # test label no other route can produce would test the wrong thing. And
        # that count is never three, so the ID always has its room.
        show_asset_id=True,
        qr_per_row=_qr_per_row_off(),
    )
    try:
        await printer.print_png(agent, png, copies=1)
    except printer.PrintError as exc:
        message, detail = _print_error_parts(str(exc), lang)
        return HTMLResponse(_print_status(request, ok=False, message=message, detail=detail))
    return HTMLResponse(_print_status(request, ok=True, message=t("test_print_sent", lang)))


@app.post("/settings/printers/{agent_id}/shutdown")
async def shutdown_agent(agent_id: str, request: Request, user: str = Depends(require_login)):
    """Power this Raspberry Pi down cleanly (it is headless)."""
    lang = get_lang(request)
    agent = agents.by_id(agent_id)
    if agent is None:
        return HTMLResponse(
            f'<span class="print-status error-text">{t("err_no_printer", lang)}</span>'
        )
    try:
        await printer.shutdown(agent)
    except printer.PrintError as exc:
        return HTMLResponse(
            f'<span class="print-status error-text">{t("shutdown_failed", lang)}: {exc}</span>'
        )
    return HTMLResponse(
        f'<span class="print-status ok-text">{t("shutdown_sent", lang)}</span>'
    )
