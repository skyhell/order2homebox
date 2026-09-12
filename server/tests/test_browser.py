"""Browser tests for the things only a real layout engine can answer.

Everything else in this suite renders HTML and reads it back, which says
nothing about how wide a control ends up or which row it lands on — the edit
page's order row had shrunk its shop picker to its arrow and no test noticed.

These need Chromium (``playwright install chromium``); without it they skip,
so the rest of the suite stays a browser-free run.
"""
import asyncio
import socket

import pytest

from tests.conftest import TEST_PASSWORD

pytest.importorskip("playwright.async_api")

PHONE = {"width": 400, "height": 900}
TABLET = {"width": 760, "height": 900}
DESKTOP = {"width": 1280, "height": 900}


@pytest.fixture()
async def live_server(monkeypatch):
    """The real app on a real port, with Homebox stubbed — the edit page asks
    it for locations and labels before it renders anything at all."""
    import uvicorn

    import app.main as main

    async def fake_locations():
        return [{"id": "loc1", "name": "Stall neu"}, {"id": "loc2", "name": "Büro"}]

    async def fake_labels():
        return [{"id": "l1", "name": "CNC"}, {"id": "l2", "name": "Druckluft"}]

    monkeypatch.setattr(main.homebox, "get_locations", fake_locations)
    monkeypatch.setattr(main.homebox, "get_labels", fake_labels)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(main.app, log_level="warning", lifespan="on")
    )
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await serving


@pytest.fixture()
async def page(live_server):
    from playwright.async_api import Error, async_playwright

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Error as exc:  # installed package, missing browser binary
            pytest.skip(f"no Chromium to drive ({exc}); run: playwright install chromium")
        context = await browser.new_page(viewport=DESKTOP)
        await context.goto(f"{live_server}/login")
        await context.fill('input[name="username"]', "admin")
        await context.fill('input[name="password"]', TEST_PASSWORD)
        await context.click('button[type="submit"]')
        await context.wait_for_url(f"{live_server}/")
        yield context
        await browser.close()


async def open_order_edit(page, live_server, shop="temu"):
    """The edit page of a fetched order — the shop is a dropdown only there.
    Seeded through the very draft the browser posts while typing, because
    fetching a real order page is the one thing a test cannot do."""
    await page.request.post(f"{live_server}/draft", form={
        "shop": shop, "order_no": "028-111", "order_date": "2026-05-20",
        "item_count": "1", "item-0-name": "USB Hub", "item-0-location": "loc1",
    })
    await page.goto(f"{live_server}/edit")


async def test_the_order_row_is_two_rows_with_a_readable_shop_picker(page, live_server):
    """All three fields in one flex row left 120 px of each third to the label
    alone, and the shop — two controls wide at "Sonstiges" — was down to its
    arrow. The shop keeps the first row now, the other two share the second."""
    await open_order_edit(page, live_server)
    shop = await page.locator("#shop-select").bounding_box()
    order_no = await page.locator('input[name="order_no"]').bounding_box()
    order_date = await page.locator('input[name="order_date"]').bounding_box()

    assert shop["width"] > 150, "the picker has to show the shop, not just its arrow"
    assert order_no["y"] > shop["y"], "the shop keeps the first row to itself"
    assert abs(order_no["y"] - order_date["y"]) < 2, "these two share the second row"
    assert order_no["width"] > 150


async def test_the_custom_shop_box_shares_the_shop_row(page, live_server):
    await open_order_edit(page, live_server)
    custom = page.locator("#shop-custom")
    assert not await custom.is_visible()  # hidden for one of the four known shops

    await page.select_option("#shop-select", "__other__")
    assert await custom.is_visible()
    box = await custom.bounding_box()
    picker = await page.locator("#shop-select").bounding_box()
    assert abs(box["y"] - picker["y"]) < 2, "it belongs beside the picker, not below"
    assert box["width"] > 150, "wide enough to read the shop being typed into it"


async def test_the_subtitle_follows_the_shop_field(page, live_server):
    """The shop became a field on this page, so the line naming it above the
    form has to change with it instead of naming what was loaded."""
    await open_order_edit(page, live_server)
    subtitle = page.locator("#order-subtitle")
    assert "Temu" in await subtitle.inner_text()

    await page.select_option("#shop-select", "banggood")
    assert "Banggood" in await subtitle.inner_text()

    await page.select_option("#shop-select", "__other__")
    await page.fill("#shop-custom", "Kleinanzeigen")
    assert "Kleinanzeigen" in await subtitle.inner_text()


async def test_the_order_row_stacks_on_a_phone(page, live_server):
    await page.set_viewport_size(PHONE)
    await open_order_edit(page, live_server)
    shop = await page.locator("#shop-select").bounding_box()
    order_no = await page.locator('input[name="order_no"]').bounding_box()
    order_date = await page.locator('input[name="order_date"]').bounding_box()

    assert shop["y"] < order_no["y"] < order_date["y"], "one field per row"
    # Measured against the card rather than the document: this test answers for
    # the order row, the bar above it has its own.
    card = await page.locator(".meta-card").bounding_box()
    assert card["x"] + card["width"] <= PHONE["width"]
    for field in (shop, order_no, order_date):
        assert field["x"] + field["width"] <= card["x"] + card["width"]


async def test_the_manual_order_row_is_readable_too(page, live_server):
    """Same two-row card, with a typed shop in place of the dropdown — the
    grid must not treat a plain input any differently."""
    await page.goto(f"{live_server}/manual")
    shop = await page.locator("#shop-text").bounding_box()
    order_no = await page.locator('input[name="order_no"]').bounding_box()
    order_date = await page.locator('input[name="order_date"]').bounding_box()

    assert shop["width"] > 150
    assert order_no["y"] > shop["y"], "the shop keeps the first row to itself"
    assert abs(order_no["y"] - order_date["y"]) < 2


async def test_the_nav_bar_never_pushes_a_page_sideways(page, live_server):
    """Between the phone breakpoint and a wide laptop every link is shown, and
    there are seven of them — in German they are wider than the bar. They wrap
    onto a second line now; before that the whole page scrolled sideways from
    1100 px down, and the words themselves were broken in half."""
    for lang in ("de", "en"):
        await page.goto(f"{live_server}/lang/{lang}")
        for size in (DESKTOP, {"width": 1024, "height": 900}, TABLET, PHONE):
            await page.set_viewport_size(size)
            await page.goto(f"{live_server}/manual")
            scrolled = await page.evaluate("document.documentElement.scrollWidth")
            assert scrolled <= size["width"], f"{lang} scrolls sideways at {scrolled}"
            links = page.locator(".nav-link")
            for i in range(await links.count()):
                box = await links.nth(i).bounding_box()
                if box:  # off screen on a phone until the row is scrolled
                    assert box["height"] < 40, "an entry was broken over two lines"
    await page.set_viewport_size(DESKTOP)
    await page.goto(f"{live_server}/lang/de")


async def test_the_nav_bar_fits_a_phone(page, live_server):
    """The brand name beside the language switch, theme toggle and logout link
    came to more than 400 px, and none of them can shrink — so every page
    scrolled sideways on a phone. The name goes, the icon stays."""
    await page.goto(f"{live_server}/manual")
    assert await page.locator(".brand-name").is_visible(), "kept where there is room"

    await page.set_viewport_size(PHONE)
    assert not await page.locator(".brand-name").is_visible()
    scrolled = await page.evaluate("document.documentElement.scrollWidth")
    assert scrolled <= PHONE["width"], "no page may scroll sideways on a phone"
    assert await page.locator(".brand-icon").is_visible(), "still the way home"
    assert await page.locator('.nav-actions a[href="/logout"]').is_visible()


async def test_every_page_can_still_be_reached_on_a_phone(page, live_server):
    """The links were hidden outright below 640 px, and this page lost the one
    link that used to lead to it from the start page — so on a phone there was
    no way in at all. They scroll sideways in a row of their own instead."""
    await page.set_viewport_size(PHONE)
    await page.goto(f"{live_server}/manual")
    links = page.locator(".nav-links")
    assert await links.is_visible()

    manual = page.locator('.nav-link[href="/manual"]')
    await manual.scroll_into_view_if_needed()
    await manual.click()
    await page.wait_for_url(f"{live_server}/manual")

    # The row scrolls, the page does not.
    assert await page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    assert await links.evaluate("el => el.scrollWidth > el.clientWidth"), (
        "the entries are meant to overflow their row, not be squeezed into it"
    )
    settings = page.locator('.nav-link[href="/settings"]')
    await settings.scroll_into_view_if_needed()
    assert await settings.is_visible()


async def test_clearing_the_fields_still_clears_them(page, live_server):
    """It is no longer a submit button, so the script has to send the form —
    and only once the auto-save already on its way has arrived, or that one
    writes the series straight back."""
    await page.goto(f"{live_server}/manual")
    await page.fill('textarea[name="item-0-name"]', "Kugellager 608")
    await page.click("text=Felder leeren")
    await page.wait_for_url(f"{live_server}/manual")
    assert "Kugellager 608" not in await page.content()

    await page.goto(f"{live_server}/manual")  # and it stays cleared
    assert "Kugellager 608" not in await page.content()


async def test_enter_on_the_manual_page_does_not_clear_the_series(page, live_server):
    """The "clear the fields" button was the first submit button in the form,
    which makes it the button Enter presses: typing a price and hitting Enter
    threw the whole series away."""
    asked = []

    async def record(route):
        asked.append(route.request.url)
        await route.abort()

    await page.route("**/create", record)
    await page.route("**/manual/reset", record)
    await page.goto(f"{live_server}/manual")
    await page.fill('textarea[name="item-0-name"]', "Kugellager 608")
    await page.fill('input[name="order_no"]', "A-1")
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(400)

    assert asked, "Enter has to submit the form, not do nothing"
    assert not any("/manual/reset" in url for url in asked), (
        f"Enter cleared the series instead of creating the item: {asked}"
    )
