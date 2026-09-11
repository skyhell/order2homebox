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


async def test_the_order_row_is_two_rows_with_a_readable_shop_picker(page, live_server):
    """All three fields in one flex row left 120 px of each third to the label
    alone, and the shop — two controls wide at "Sonstiges" — was down to its
    arrow. The shop keeps the first row now, the other two share the second."""
    await page.goto(f"{live_server}/manual?shop=temu")
    shop = await page.locator("#shop-select").bounding_box()
    order_no = await page.locator('input[name="order_no"]').bounding_box()
    order_date = await page.locator('input[name="order_date"]').bounding_box()

    assert shop["width"] > 150, "the picker has to show the shop, not just its arrow"
    assert order_no["y"] > shop["y"], "the shop keeps the first row to itself"
    assert abs(order_no["y"] - order_date["y"]) < 2, "these two share the second row"
    assert order_no["width"] > 150


async def test_the_custom_shop_box_shares_the_shop_row(page, live_server):
    await page.goto(f"{live_server}/manual?shop=temu")
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
    await page.goto(f"{live_server}/manual?shop=temu")
    subtitle = page.locator("#order-subtitle")
    assert "Temu" in await subtitle.inner_text()

    await page.select_option("#shop-select", "banggood")
    assert "Banggood" in await subtitle.inner_text()

    await page.select_option("#shop-select", "__other__")
    await page.fill("#shop-custom", "Kleinanzeigen")
    assert "Kleinanzeigen" in await subtitle.inner_text()


async def test_the_order_row_stacks_on_a_phone(page, live_server):
    await page.set_viewport_size(PHONE)
    await page.goto(f"{live_server}/manual?shop=temu")
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


async def test_the_nav_bar_fits_a_phone(page, live_server):
    """The brand name beside the language switch, theme toggle and logout link
    came to more than 400 px, and none of them can shrink — so every page
    scrolled sideways on a phone. The name goes, the icon stays."""
    await page.goto(f"{live_server}/manual?shop=temu")
    assert await page.locator(".brand-name").is_visible(), "kept where there is room"

    await page.set_viewport_size(PHONE)
    assert not await page.locator(".brand-name").is_visible()
    scrolled = await page.evaluate("document.documentElement.scrollWidth")
    assert scrolled <= PHONE["width"], "no page may scroll sideways on a phone"
    assert await page.locator(".brand-icon").is_visible(), "still the way home"
    assert await page.locator('.nav-actions a[href="/logout"]').is_visible()
