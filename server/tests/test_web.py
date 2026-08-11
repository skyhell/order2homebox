import io
import re

import pytest

from tests.conftest import TEST_PASSWORD


def test_unauthenticated_redirects_to_login(client):
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_htmx_request_gets_hx_redirect(client):
    response = client.post("/print", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"


def test_login_wrong_password(client):
    response = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert response.status_code == 200
    assert "falsch" in response.text  # German default


def test_login_and_index(logged_in):
    response = logged_in.get("/")
    assert response.status_code == 200
    assert "Bestellung erfassen" in response.text


def test_language_toggle(logged_in):
    response = logged_in.get("/lang/en", headers={"referer": "/"})
    assert response.status_code == 303
    response = logged_in.get("/")
    assert "Capture an order" in response.text
    logged_in.get("/lang/de", headers={"referer": "/"})


def test_health_needs_no_auth(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_label_preview_requires_valid_asset_id(logged_in):
    ok = logged_in.get("/label/000-123.png")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"
    bad = logged_in.get("/label/../../etc.png")
    assert bad.status_code in (404, 422)


def test_login_page_available(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "order2homebox" in response.text


def test_create_single_item_returns_result_fragment(logged_in, monkeypatch):
    """Per-item button: creates one item in Homebox, prints, swaps in result card."""
    import app.main as main

    created_with = {}

    async def fake_create_item(draft, order, location_id, label_ids):
        created_with.update(
            name=draft.name, price=draft.unit_price, location=location_id,
            labels=label_ids, order_no=order.order_no,
        )
        return {"id": "item1", "assetId": "000-007"}

    async def fake_print(png, copies=1):
        created_with["printed_copies"] = copies
        return {"status": "printed"}

    monkeypatch.setattr(main.homebox, "create_item", fake_create_item)
    monkeypatch.setattr(main.printer, "print_png", fake_print)

    response = logged_in.post("/create-item", data={
        "idx": "1", "shop": "amazon", "order_no": "028-111", "order_date": "",
        "item_count": "2",
        "item-1-name": "USB Hub", "item-1-quantity": "1", "item-1-price": "16,27",
        "item-1-location": "loc1", "item-1-labels": "lab1", "item-1-print": "on",
    })
    assert response.status_code == 200
    assert 'id="item-card-1"' in response.text
    assert "000-007" in response.text
    assert created_with == {
        "name": "USB Hub", "price": 16.27, "location": "loc1",
        "labels": ["lab1"], "order_no": "028-111", "printed_copies": 1,
    }


def _stub_create_and_print(monkeypatch, rendered):
    """Homebox + printer stubbed out; records how the label was rendered."""
    import app.main as main

    async def fake_create_item(draft, order, location_id, label_ids):
        return {"id": "item1", "assetId": "000-007"}

    async def fake_print(png, copies=1):
        return {"status": "printed"}

    def fake_render(asset_id, url, show_asset_id=True, qr_per_row=2):
        rendered["show_asset_id"] = show_asset_id
        return b"PNG"

    monkeypatch.setattr(main.homebox, "create_item", fake_create_item)
    monkeypatch.setattr(main.printer, "print_png", fake_print)
    monkeypatch.setattr(main, "render_label_png", fake_render)


def test_item_card_asset_id_checkbox_controls_the_printed_label(logged_in, monkeypatch):
    """Per item: unticking the asset-ID box must print a bare QR code, and the
    result card must then show that same label — not the configured default."""
    rendered = {}
    _stub_create_and_print(monkeypatch, rendered)

    data = {
        "idx": "1", "shop": "amazon", "order_no": "028-111", "order_date": "",
        "item_count": "2", "item-1-name": "USB Hub", "item-1-quantity": "1",
        "item-1-print": "on",
    }
    response = logged_in.post("/create-item", data=data)  # no item-1-showid
    assert rendered["show_asset_id"] is False
    assert "?text=0" in response.text
    assert 'id="show-text-1" checked' not in response.text

    response = logged_in.post("/create-item", data={**data, "item-1-showid": "on"})
    assert rendered["show_asset_id"] is True
    assert "?text=1" in response.text
    assert 'id="show-text-1" checked' in response.text


def _stub_create_and_capture(monkeypatch, captured):
    """Records how each label was rendered, per item."""
    import app.main as main

    async def fake_create_item(draft, order, location_id, label_ids):
        return {"id": "item1", "assetId": "000-007"}

    async def fake_print(png, copies=1):
        return {"status": "printed"}

    def fake_render(asset_id, url, show_asset_id=True, qr_per_row=2):
        captured.append({"show_asset_id": show_asset_id, "qr_per_row": qr_per_row})
        return b"PNG"

    monkeypatch.setattr(main.homebox, "create_item", fake_create_item)
    monkeypatch.setattr(main.printer, "print_png", fake_print)
    monkeypatch.setattr(main, "render_label_png", fake_render)


def test_item_card_three_up_checkbox_prints_three_codes(logged_in, monkeypatch):
    """Small parts you have several of: three codes across the width."""
    captured = []
    _stub_create_and_capture(monkeypatch, captured)
    logged_in.post("/create-item", data={
        "idx": "1", "shop": "amazon", "order_no": "028-111", "order_date": "",
        "item_count": "2", "item-1-name": "O-Ring", "item-1-quantity": "1",
        "item-1-print": "on", "item-1-qr3": "on",
    })
    assert captured == [{"show_asset_id": False, "qr_per_row": 3}]


def test_three_up_wins_over_the_asset_id_checkbox(logged_in, monkeypatch):
    """A three-up cell is 102 px and an asset id can be 121 px — printing both
    would lay the text across the neighbouring code. The form cannot ask for
    the combination even if the checkbox is somehow submitted."""
    captured = []
    _stub_create_and_capture(monkeypatch, captured)
    logged_in.post("/create-item", data={
        "idx": "1", "shop": "amazon", "order_no": "028-111", "order_date": "",
        "item_count": "2", "item-1-name": "O-Ring", "item-1-quantity": "1",
        "item-1-print": "on", "item-1-qr3": "on", "item-1-showid": "on",
    })
    assert captured == [{"show_asset_id": False, "qr_per_row": 3}]


def test_without_the_checkbox_the_configured_count_is_used(logged_in, monkeypatch):
    captured = []
    _stub_create_and_capture(monkeypatch, captured)
    logged_in.post("/create-item", data={
        "idx": "1", "shop": "amazon", "order_no": "028-111", "order_date": "",
        "item_count": "2", "item-1-name": "USB Hub", "item-1-quantity": "1",
        "item-1-print": "on", "item-1-showid": "on",
    })
    from app.config import settings

    assert captured == [
        {"show_asset_id": True, "qr_per_row": settings.label_qr_per_row}
    ]


def test_three_up_travels_into_the_result_card(logged_in, monkeypatch):
    """The result card must reprint what was printed, not the default."""
    _stub_create_and_capture(monkeypatch, [])
    body = logged_in.post("/create-item", data={
        "idx": "1", "shop": "amazon", "order_no": "028-111", "order_date": "",
        "item_count": "2", "item-1-name": "O-Ring", "item-1-quantity": "1",
        "item-1-print": "on", "item-1-qr3": "on",
    }).text
    assert "count=3" in body  # preview
    assert "qr_per_row: 3" in body  # reprint
    assert 'class="check hidden"' in body  # the id checkbox is not on offer


def test_reprinting_three_up_never_adds_the_asset_id(logged_in, monkeypatch):
    """Even if the POST asks for both — the print route is reachable directly."""
    import app.main as main

    captured = []

    def fake_render(asset_id, url, show_asset_id=True, qr_per_row=2):
        captured.append({"show_asset_id": show_asset_id, "qr_per_row": qr_per_row})
        return b"PNG"

    async def fake_print(png, copies=1):
        return {"status": "printed"}

    monkeypatch.setattr(main, "render_label_png", fake_render)
    monkeypatch.setattr(main.printer, "print_png", fake_print)

    logged_in.post("/print", data={
        "asset_id": "000-007", "copies": "1", "show_text": "true", "qr_per_row": "3",
    })
    assert captured == [{"show_asset_id": False, "qr_per_row": 3}]


def test_create_all_items_prints_each_with_its_own_asset_id_choice(logged_in, monkeypatch):
    calls = []
    import app.main as main

    async def fake_create_item(draft, order, location_id, label_ids):
        return {"id": "item1", "assetId": "000-00%d" % len(calls)}

    async def fake_print(png, copies=1):
        return {"status": "printed"}

    def fake_render(asset_id, url, show_asset_id=True, qr_per_row=2):
        calls.append(show_asset_id)
        return b"PNG"

    monkeypatch.setattr(main.homebox, "create_item", fake_create_item)
    monkeypatch.setattr(main.printer, "print_png", fake_print)
    monkeypatch.setattr(main, "render_label_png", fake_render)

    logged_in.post("/create", data={
        "shop": "amazon", "order_no": "028-111", "order_date": "", "item_count": "2",
        "item-0-name": "With id", "item-0-quantity": "1",
        "item-0-print": "on", "item-0-showid": "on",
        "item-1-name": "Without id", "item-1-quantity": "1", "item-1-print": "on",
    })
    assert calls == [True, False]


def test_edit_page_offers_the_asset_id_checkbox_per_item(logged_in, monkeypatch):
    import app.main as main

    async def fake_empty():
        return []

    monkeypatch.setattr(main.homebox, "get_locations", fake_empty)
    monkeypatch.setattr(main.homebox, "get_labels", fake_empty)

    body = logged_in.get("/manual").text
    assert 'name="item-0-print"' in body
    assert 'name="item-0-showid"' in body
    # follows LABEL_SHOW_ASSET_ID, which defaults to on
    assert "checked" in body.split('name="item-0-showid"')[1].split("</label>")[0]


def test_item_card_wires_quantity_to_the_price(logged_in, monkeypatch):
    """The re-split happens in the browser, so the card has to carry the hooks
    app.js looks for — an id per field and the two handlers."""
    import app.main as main

    async def fake_empty():
        return []

    monkeypatch.setattr(main.homebox, "get_locations", fake_empty)
    monkeypatch.setattr(main.homebox, "get_labels", fake_empty)

    body = logged_in.get("/manual").text
    assert 'id="qty-0"' in body and 'id="price-0"' in body
    assert "repriceItem('0')" in body  # quantity changed -> re-split
    assert "rebaseItemTotal('0')" in body  # price edited -> new sum
    assert 'id="total-0"' in body  # and the sum is shown, not silent


def test_item_name_is_a_growing_text_box(logged_in, monkeypatch):
    """Marketplace names run to 200 characters; in a one-line input the end can
    only be reached by scrolling inside the line. The field is a textarea that
    app.js grows — the class is the hook it looks for."""
    import app.main as main

    async def fake_empty():
        return []

    monkeypatch.setattr(main.homebox, "get_locations", fake_empty)
    monkeypatch.setattr(main.homebox, "get_labels", fake_empty)

    body = logged_in.get("/manual").text
    assert '<textarea class="input autogrow" name="item-0-name"' in body
    assert '<textarea class="input autogrow" name="item-0-description"' in body


def test_a_line_break_in_the_name_does_not_reach_homebox(logged_in, monkeypatch):
    """The name field takes line breaks now (typed or pasted); an item title in
    Homebox is a single line."""
    import app.main as main

    created_with = {}

    async def fake_create_item(draft, order, location_id, label_ids):
        created_with["name"] = draft.name
        return {"id": "item1", "assetId": "000-007"}

    monkeypatch.setattr(main.homebox, "create_item", fake_create_item)

    response = logged_in.post("/create-item", data={
        "idx": "1", "shop": "amazon", "order_no": "028-111", "order_date": "",
        "item_count": "2", "item-1-name": "USB Hub\r\nmit 4K HDMI  ",
        "item-1-quantity": "1",
    })
    assert response.status_code == 200
    assert created_with["name"] == "USB Hub mit 4K HDMI"


def test_result_card_print_controls_carry_no_form_field_names(logged_in, monkeypatch):
    """The result card is swapped into #create-form, and htmx adds the enclosing
    form's fields to every POST — they even override hx-include. Named inputs
    would collide across cards, so each card's button printed the LAST card's
    asset id. Values must travel via hx-vals instead."""
    import app.main as main

    async def fake_create_item(draft, order, location_id, label_ids):
        return {"id": "item1", "assetId": "000-007"}

    async def fake_print(png, copies=1):
        return {"status": "printed"}

    monkeypatch.setattr(main.homebox, "create_item", fake_create_item)
    monkeypatch.setattr(main.printer, "print_png", fake_print)

    response = logged_in.post("/create-item", data={
        "idx": "1", "shop": "amazon", "order_no": "028-111", "order_date": "",
        "item_count": "2", "item-1-name": "USB Hub", "item-1-quantity": "1",
    })
    body = response.text
    for name in ("asset_id", "copies", "show_text"):
        assert f'name="{name}"' not in body
    assert 'id="copies-1"' in body and 'id="show-text-1"' in body
    assert 'asset_id: "000-007"' in body  # the card's own id, not a shared field
    # the preview must follow the checkbox, else it shows a label that is not printed
    assert 'id="preview-1"' in body
    assert "labelPreview('preview-1', '000-007', this.checked, 2)" in body


def test_create_single_item_without_name_keeps_card(logged_in, monkeypatch):
    import app.main as main

    async def fake_empty():
        return []

    monkeypatch.setattr(main.homebox, "get_locations", fake_empty)
    monkeypatch.setattr(main.homebox, "get_labels", fake_empty)

    response = logged_in.post("/create-item", data={
        "idx": "0", "shop": "amazon", "order_no": "", "order_date": "",
        "item_count": "1", "item-0-name": "  ", "item-0-quantity": "1",
    })
    assert response.status_code == 200
    assert 'id="item-card-0"' in response.text
    assert 'name="item-0-name"' in response.text  # still editable
    assert "Namen" in response.text  # German error message


def test_manual_edit_page_renders_item_card(logged_in, monkeypatch):
    import app.main as main

    async def fake_locations():
        return [{"id": "loc1", "name": "Büro"}]

    async def fake_labels():
        return [{"id": "lab1", "name": "Elektronik"}]

    monkeypatch.setattr(main.homebox, "get_locations", fake_locations)
    monkeypatch.setattr(main.homebox, "get_labels", fake_labels)

    response = logged_in.get("/manual?shop=temu")
    assert response.status_code == 200
    assert 'id="item-card-0"' in response.text
    assert 'hx-post="/create-item"' in response.text  # per-item button present
    assert "Büro" in response.text and "Elektronik" in response.text


def test_static_assets_are_stamped_so_an_update_reaches_the_browser(logged_in):
    """Without a stamp the browser may keep the old app.js for hours after an
    update — and the app version cannot serve as one, fixes ship between
    releases."""
    import re

    import app.main as main

    body = logged_in.get("/").text
    for name in ("app.css", "app.js", "htmx.min.js"):
        assert re.search(rf"/static/{re.escape(name)}\?v=\d+", body), name

    js = main.asset_url("app.js")
    (main.BASE_DIR / "static" / "app.js").touch()
    assert main.asset_url("app.js") != js  # a changed file gets a new URL
    assert main.asset_url("does-not-exist.js") == "/static/does-not-exist.js"


def test_footer_shows_version_and_docs_link(logged_in):
    import app

    response = logged_in.get("/")
    assert f"v{app.__version__}" in response.text
    assert "github.com/skyhell/order2homebox" in response.text
    assert 'class="footer"' in response.text


def test_footer_present_on_login_page(client):
    import app

    response = client.get("/login")
    assert 'class="footer"' in response.text
    assert f"v{app.__version__}" in response.text


def test_label_tool_page_renders(logged_in):
    response = logged_in.get("/label")
    assert response.status_code == 200
    assert 'hx-post="/label/resolve"' in response.text
    assert 'name="link"' in response.text


def test_label_resolve_from_asset_deep_link(logged_in):
    response = logged_in.post(
        "/label/resolve", data={"link": "https://box.example.com/a/000-629"}
    )
    assert response.status_code == 200
    assert "000-629" in response.text
    assert 'hx-post="/print"' in response.text  # ready-to-print controls
    assert "/label/000-629.png" in response.text  # preview image


def test_label_reprint_offers_three_up(logged_in):
    """Same choice as on an item card. The count rides along as a plain form
    value, so hx-include carries it without any JS."""
    body = logged_in.post("/label/resolve", data={"link": "000-629"}).text
    assert 'name="qr_per_row" value="3"' in body
    assert 'id="label-qr3"' in body and 'id="label-show-text"' in body
    # both boxes drive the same refresh, so the preview follows either
    assert body.count("refreshLabelControls('000-629')") == 2


def test_label_resolve_from_bare_asset_id(logged_in):
    response = logged_in.post("/label/resolve", data={"link": "000-629"})
    assert response.status_code == 200
    assert 'value="000-629"' in response.text


def test_label_resolve_from_item_url_looks_up_asset(logged_in, monkeypatch):
    import app.main as main

    captured = {}

    async def fake_get_item(item_id):
        captured["id"] = item_id
        return {"id": item_id, "assetId": "000-042"}

    monkeypatch.setattr(main.homebox, "get_item", fake_get_item)
    uuid = "a23e834c-861a-42c4-b57c-59aa607e78c3"
    response = logged_in.post(
        "/label/resolve", data={"link": f"https://box.example.com/item/{uuid}"}
    )
    assert response.status_code == 200
    assert captured["id"] == uuid
    assert "000-042" in response.text


def test_label_resolve_item_without_asset_id_shows_error(logged_in, monkeypatch):
    import app.main as main

    async def fake_get_item(item_id):
        return {"id": item_id, "assetId": "000-000"}  # unassigned

    monkeypatch.setattr(main.homebox, "get_item", fake_get_item)
    uuid = "a23e834c-861a-42c4-b57c-59aa607e78c3"
    response = logged_in.post("/label/resolve", data={"link": f"/item/{uuid}"})
    assert response.status_code == 200
    assert "banner-error" in response.text
    assert "Asset-ID" in response.text  # German default


@pytest.mark.parametrize("asset_id", ["000-629", "12345-678", "000-001"])
def test_label_resolve_accepts_ids_homebox_hands_out(logged_in, asset_id):
    response = logged_in.post("/label/resolve", data={"link": asset_id})
    assert f'value="{asset_id}"' in response.text


@pytest.mark.parametrize("typo", ["000-62", "000-6290", "00-629", "0-1"])
def test_label_resolve_rejects_a_mistyped_asset_id(logged_in, typo):
    """Homebox always pads to {3+}-{3}. A label printed from a shorter or
    longer id carries a QR code that resolves to nothing, so the typo has to
    surface here rather than on the tape."""
    response = logged_in.post("/label/resolve", data={"link": typo})
    assert "banner-error" in response.text


def test_label_resolve_rejects_a_deep_link_with_a_mistyped_id(logged_in):
    """The link must not be trimmed down to the part that happens to look
    valid — that would print 000-629 for a scan of 000-6290."""
    response = logged_in.post(
        "/label/resolve", data={"link": "https://box.example.com/a/000-6290"}
    )
    assert "banner-error" in response.text
    # the trimmed id must not come back as something ready to print
    assert 'value="000-629"' not in response.text
    assert 'hx-post="/print"' not in response.text


def test_label_preview_rejects_a_mistyped_asset_id(logged_in):
    assert logged_in.get("/label/000-62.png").status_code == 404


def test_label_resolve_unrecognized_input_shows_error(logged_in):
    response = logged_in.post("/label/resolve", data={"link": "not a link"})
    assert response.status_code == 200
    assert "banner-error" in response.text


def test_label_resolve_requires_login(client):
    response = client.post(
        "/label/resolve",
        data={"link": "000-1"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"


# -- text-only labels ---------------------------------------------------------


def _clear_text_prefs():
    """Reset everything the text page remembers, so tests do not inherit the
    history or the height mode from whichever ran before them."""
    from app import prefs

    data = prefs._read()
    keys = (prefs.TEXT_LABELS, prefs.TEXT_HEIGHT_MODE, prefs.TEXT_KEEP_HEIGHT)
    for key in keys + prefs.TEXT_LINES:
        data.pop(key, None)
    prefs._write(data)


def _clear_prefs(*keys):
    """Drop single preference keys, so a history test starts from nothing."""
    from app import prefs

    data = prefs._read()
    for key in keys:
        data.pop(key, None)
    prefs._write(data)


def _is_checked(page: str, element_id: str) -> bool:
    """Whether that one input carries `checked` — not just the page somewhere."""
    tag = re.search(r'<input[^>]*id="%s"[^>]*>' % re.escape(element_id), page)
    return tag is not None and "checked" in tag.group(0)


def _stub_printer(monkeypatch):
    import app.main as main

    printed = []

    async def fake_print(png, copies=1):
        printed.append((png, copies))

    monkeypatch.setattr(main.printer, "print_png", fake_print)
    return printed


def test_text_label_page_renders(logged_in):
    response = logged_in.get("/text")
    assert response.status_code == 200
    assert 'hx-post="/text/print"' in response.text
    assert 'id="text-line1"' in response.text
    assert 'id="text-line2"' in response.text


def test_text_preview_renders_a_png(logged_in):
    response = logged_in.get("/text.png", params={"line1": "Schrauben", "line2": "M4"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_text_preview_without_text_is_not_an_error_image(logged_in):
    """An empty form must not render a blank label — there is nothing to show."""
    assert logged_in.get("/text.png").status_code == 404
    assert logged_in.get("/text.png", params={"line1": "   "}).status_code == 404


def test_text_print_sends_the_label_and_remembers_it(logged_in, monkeypatch):
    _clear_text_prefs()
    printed = _stub_printer(monkeypatch)

    response = logged_in.post(
        "/text/print", data={"line1": "Schrauben", "line2": "M4 x 20", "copies": "2"}
    )
    assert response.status_code == 200
    assert "ok-text" in response.text
    assert len(printed) == 1 and printed[0][1] == 2
    assert printed[0][0].startswith(b"\x89PNG")

    from app import prefs

    assert prefs.get_text_labels()[0] == ["Schrauben", "M4 x 20"]
    # the fresh history rides along, so the chip appears without a reload
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert "Schrauben" in response.text


def test_text_print_history_shows_up_on_the_page(logged_in, monkeypatch):
    _clear_text_prefs()
    _stub_printer(monkeypatch)
    logged_in.post("/text/print", data={"line1": "Kabel", "line2": "USB-C"})

    response = logged_in.get("/text")
    assert 'data-line1="Kabel"' in response.text
    assert 'data-line2="USB-C"' in response.text


def test_text_print_does_not_remember_a_label_that_failed(logged_in, monkeypatch):
    """The history is a list of labels that exist — a print that never came out
    must not put text there."""
    import app.main as main

    _clear_text_prefs()

    async def fake_print(png, copies=1):
        raise main.printer.PrintError("agent unreachable")

    monkeypatch.setattr(main.printer, "print_png", fake_print)
    response = logged_in.post("/text/print", data={"line1": "Fehldruck"})
    assert "error-text" in response.text

    from app import prefs

    assert prefs.get_text_labels() == []


def test_text_print_without_text_is_refused(logged_in, monkeypatch):
    printed = _stub_printer(monkeypatch)
    response = logged_in.post("/text/print", data={"line1": "  ", "line2": ""})
    assert "error-text" in response.text
    assert printed == []


def test_text_history_keeps_a_repeat_once_and_first(logged_in, monkeypatch):
    _clear_text_prefs()
    _stub_printer(monkeypatch)
    for line1 in ("Kabel", "Schrauben", "Kabel"):
        logged_in.post("/text/print", data={"line1": line1})

    from app import prefs

    assert prefs.get_text_labels() == [["Kabel"], ["Schrauben"]]


def _png_height(content):
    from PIL import Image

    return Image.open(io.BytesIO(content)).height


def test_text_page_offers_both_height_checkboxes(logged_in):
    response = logged_in.get("/text")
    assert 'id="text-keep-height"' in response.text
    assert 'id="text-force-height"' in response.text


def test_text_preview_follows_every_height_mode(logged_in):
    """The preview must show what would be printed, so the mode has to reach
    the renderer — not just the print route."""
    text = {"line1": "Werkstatt", "line2": "Regal 3"}
    heights = {}
    for mode in ("grow", "keep", "force"):
        response = logged_in.get("/text.png", params={**text, "height": mode})
        assert response.status_code == 200
        heights[mode] = _png_height(response.content)
    assert heights["force"] < heights["keep"] < heights["grow"]


def test_text_preview_ignores_a_bogus_height_mode(logged_in):
    """A hand-edited URL must not print something nobody chose."""
    text = {"line1": "Werkstatt", "line2": "Regal 3"}
    bogus = logged_in.get("/text.png", params={**text, "height": "../etc"})
    plain = logged_in.get("/text.png", params=text)
    assert bogus.status_code == 200
    assert bogus.content == plain.content


@pytest.mark.parametrize("mode", ["keep", "force"])
def test_text_print_uses_the_height_mode_and_remembers_it(logged_in, monkeypatch, mode):
    _clear_text_prefs()
    printed = _stub_printer(monkeypatch)

    from app import prefs
    from app.labels import render_text_label_png

    logged_in.post(
        "/text/print",
        data={"line1": "Werkstatt", "line2": "Regal 3", "height_mode": mode},
    )
    assert printed[0][0] == render_text_label_png(["Werkstatt", "Regal 3"], mode)
    assert prefs.get_text_height_mode() == mode

    page = logged_in.get("/text").text
    assert _is_checked(page, "text-keep-height")
    assert _is_checked(page, "text-force-height") is (mode == "force")


def test_text_print_without_a_mode_goes_back_to_growing(logged_in, monkeypatch):
    """The remembered mode follows the label that was actually printed, so
    unticking has to stick as well."""
    _clear_text_prefs()
    _stub_printer(monkeypatch)

    from app import prefs

    logged_in.post("/text/print", data={"line1": "a", "line2": "b", "height_mode": "force"})
    assert prefs.get_text_height_mode() == "force"
    logged_in.post("/text/print", data={"line1": "a", "line2": "b"})
    assert prefs.get_text_height_mode() == "grow"


def test_the_old_two_state_preference_still_reads(logged_in):
    """Anyone who printed with the previous version has text_keep_height in
    prefs.json; that must not silently reset their choice on update."""
    from app import prefs

    _clear_text_prefs()
    data = prefs._read()
    data[prefs.TEXT_KEEP_HEIGHT] = True
    prefs._write(data)
    try:
        assert prefs.get_text_height_mode() == "keep"
        assert _is_checked(logged_in.get("/text").text, "text-keep-height")
    finally:
        _clear_text_prefs()


def test_the_force_row_is_hidden_until_the_box_above_is_ticked(logged_in):
    """It refines the checkbox above it, so on its own it means nothing."""
    _clear_text_prefs()
    assert 'class="force-height-row hidden"' in logged_in.get("/text").text


def test_text_print_requires_login(client):
    response = client.post(
        "/text/print", data={"line1": "x"}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"


# -- what was typed into a field before ---------------------------------------


def test_a_field_history_keeps_the_newest_first_without_duplicates(tmp_path, monkeypatch):
    from app import prefs
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    for asset_id in ("000-001", "000-002", "000-001"):
        prefs.remember_asset_ref(asset_id)
    assert prefs.get_asset_refs() == ["000-001", "000-002"]

    for n in range(12):
        prefs.remember_asset_ref(f"000-{n:03d}")
    history = prefs.get_asset_refs()
    assert len(history) == prefs.HISTORY_MAX
    assert history[0] == "000-011"  # the last one entered is on top


def test_an_order_number_is_remembered_with_its_shop(tmp_path, monkeypatch):
    """The same number at another shop is a different order, and picking one
    has to bring its shop along."""
    from app import prefs
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    prefs.remember_order("amazon", "028-111")
    prefs.remember_order("banggood", "028-111")
    assert prefs.get_orders() == [
        {"shop": "banggood", "order_no": "028-111"},
        {"shop": "amazon", "order_no": "028-111"},
    ]


def test_an_unknown_shop_in_prefs_does_not_break_the_start_page(logged_in):
    """A shop dropped from the app must not take the page down with it."""
    from app import prefs

    _clear_prefs(prefs.ORDERS)
    data = prefs._read()
    data[prefs.ORDERS] = [{"shop": "gone", "order_no": "1"}, {"shop": "temu", "order_no": "2"}]
    prefs._write(data)
    try:
        body = logged_in.get("/").text
        assert 'data-shop="temu"' in body
        assert 'data-shop="gone"' not in body
    finally:
        _clear_prefs(prefs.ORDERS)


def test_fetching_an_order_puts_the_number_in_the_history(logged_in, monkeypatch):
    import app.main as main
    from app import prefs
    from app.models import Order, OrderItemDraft

    _clear_prefs(prefs.ORDERS)

    class FakeScraper:
        async def fetch_order(self, order_no):
            return Order(shop=main.Shop.amazon, order_no=order_no,
                         items=[OrderItemDraft(name="Thing")])

    async def fake_empty():
        return []

    monkeypatch.setattr(main, "get_scraper", lambda shop: FakeScraper())
    monkeypatch.setattr(main.homebox, "get_locations", fake_empty)
    monkeypatch.setattr(main.homebox, "get_labels", fake_empty)

    logged_in.post("/fetch", data={"shop": "amazon", "order_no": "028-1674448-8402738"})
    assert prefs.get_orders()[0] == {"shop": "amazon", "order_no": "028-1674448-8402738"}

    body = logged_in.get("/").text
    assert 'data-value="028-1674448-8402738"' in body
    assert 'data-shop="amazon"' in body  # a click sets the shop as well
    _clear_prefs(prefs.ORDERS)


def test_a_failed_fetch_is_not_remembered(logged_in, monkeypatch):
    """Only what a shop really answered for is worth offering again."""
    import app.main as main
    from app import prefs
    from app.scrapers.base import SessionExpired

    _clear_prefs(prefs.ORDERS)

    class ExpiredScraper:
        async def fetch_order(self, order_no):
            raise SessionExpired("cookies gone")

    monkeypatch.setattr(main, "get_scraper", lambda shop: ExpiredScraper())
    logged_in.post("/fetch", data={"shop": "amazon", "order_no": "028-999"})
    assert prefs.get_orders() == []


def test_resolving_a_label_remembers_the_asset_id(logged_in):
    """The resolved ID is kept, not the pasted link — short and re-resolvable.
    The refreshed list rides back out of band, so it is there without a reload."""
    from app import prefs

    _clear_prefs(prefs.ASSET_REFS)
    response = logged_in.post(
        "/label/resolve", data={"link": "https://box.example.com/a/000-629"}
    )
    assert prefs.get_asset_refs() == ["000-629"]
    assert 'id="history-label-link" hx-swap-oob="outerHTML"' in response.text
    assert 'data-value="000-629"' in response.text

    assert 'data-target="label-link"' in logged_in.get("/label").text
    _clear_prefs(prefs.ASSET_REFS)


def test_a_rejected_label_reference_is_not_remembered(logged_in):
    from app import prefs

    _clear_prefs(prefs.ASSET_REFS)
    logged_in.post("/label/resolve", data={"link": "not an id"})
    assert prefs.get_asset_refs() == []


def test_printing_text_fills_both_line_histories(logged_in, monkeypatch):
    """Per line, on top of the pair chips: line 1 can then be combined with a
    new second line instead of only reprinting the whole label."""
    _clear_text_prefs()
    _stub_printer(monkeypatch)

    from app import prefs

    logged_in.post("/text/print", data={"line1": "Werkstatt", "line2": "Regal 3"})
    response = logged_in.post("/text/print", data={"line1": "Keller", "line2": "Fach 2"})

    assert prefs.get_text_line_history(0) == ["Keller", "Werkstatt"]
    assert prefs.get_text_line_history(1) == ["Fach 2", "Regal 3"]
    # both lists come back with the print response, so no reload is needed
    assert 'id="history-text-line1" hx-swap-oob="outerHTML"' in response.text
    assert 'id="history-text-line2" hx-swap-oob="outerHTML"' in response.text
    # and the whole-label chips are untouched
    assert prefs.get_text_labels()[0] == ["Keller", "Fach 2"]

    body = logged_in.get("/text").text
    assert 'data-target="text-line1" data-value="Keller"' in body
    assert 'data-target="text-line2" data-value="Fach 2"' in body
    assert 'data-line1="Keller"' in body  # the pair chip is still there
    _clear_text_prefs()


def test_fetch_crash_shows_error_banner_not_500(logged_in, monkeypatch):
    """Unexpected scraper exceptions must render an error banner, not a 500."""
    import app.main as main

    class ExplodingScraper:
        async def fetch_order(self, order_no):
            raise RuntimeError("boom")

    monkeypatch.setattr(main, "get_scraper", lambda shop: ExplodingScraper())
    response = logged_in.post(
        "/fetch", data={"shop": "amazon", "order_no": "028-1674448-8402738"}
    )
    assert response.status_code == 200
    assert "boom" in response.text


def test_shutdown_agent_reports_success(logged_in, monkeypatch):
    """The Pi is headless — the settings page must be able to power it down."""
    import app.main as main

    called = {}

    async def fake_shutdown():
        called["yes"] = True
        return {"status": "shutting_down"}

    monkeypatch.setattr(main.printer, "shutdown", fake_shutdown)
    response = logged_in.post("/settings/shutdown-agent")
    assert response.status_code == 200
    assert called == {"yes": True}
    assert "fährt herunter" in response.text  # German default


def test_shutdown_agent_shows_error_instead_of_500(logged_in, monkeypatch):
    import app.main as main

    async def fake_shutdown():
        raise main.printer.PrintError("Print agent unreachable")

    monkeypatch.setattr(main.printer, "shutdown", fake_shutdown)
    response = logged_in.post("/settings/shutdown-agent")
    assert response.status_code == 200
    assert "error-text" in response.text
    assert "unreachable" in response.text


def test_shutdown_agent_requires_login(client):
    response = client.post("/settings/shutdown-agent", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"


def test_settings_page_offers_the_shutdown_button(logged_in, monkeypatch):
    import app.main as main

    async def fake_status():
        return None

    async def fake_health():
        return {"ok": True, "dry_run": False}

    monkeypatch.setattr(main.homebox, "status", fake_status)
    monkeypatch.setattr(main.printer, "health", fake_health)
    response = logged_in.get("/settings")
    assert 'hx-post="/settings/shutdown-agent"' in response.text
    assert "hx-confirm=" in response.text  # never power off on a stray click


def test_agent_status_fragment_keeps_polling_itself(logged_in, monkeypatch):
    """The settings page must recover on its own after the Pi is switched back
    on, so the swapped-in fragment has to carry the poll trigger again."""
    import app.main as main

    async def fake_health():
        return {"ok": True, "dry_run": False}

    monkeypatch.setattr(main.printer, "health", fake_health)
    response = logged_in.get("/settings/agent-status")
    assert response.status_code == 200
    assert 'hx-get="/settings/agent-status"' in response.text
    assert 'hx-trigger="every 10s"' in response.text
    assert "status-dot ok" in response.text


def test_agent_status_fragment_shows_the_pi_as_down(logged_in, monkeypatch):
    import app.main as main

    async def fake_health():
        return {"ok": False, "error": "Connection refused"}

    monkeypatch.setattr(main.printer, "health", fake_health)
    response = logged_in.get("/settings/agent-status")
    assert "status-dot err" in response.text
    assert "Connection refused" in response.text


def test_agent_status_fragment_requires_login(client):
    response = client.get("/settings/agent-status", headers={"HX-Request": "true"})
    assert response.status_code == 401


def test_agent_status_fragment_is_never_cached(logged_in, monkeypatch):
    """A polled fragment served from the browser cache would show a stale Pi."""
    import app.main as main

    async def fake_health():
        return {"ok": True, "dry_run": False}

    monkeypatch.setattr(main.printer, "health", fake_health)
    response = logged_in.get("/settings/agent-status")
    assert response.headers["cache-control"] == "no-store"


def test_settings_page_does_not_wait_for_the_pi(logged_in, monkeypatch):
    """With the Pi off the health check burns its full timeout, so the page
    load must not touch it — the row fetches its own state afterwards."""
    import app.main as main

    async def fake_status():
        return None

    async def exploding_health():
        raise AssertionError("settings page must not block on the print agent")

    monkeypatch.setattr(main.homebox, "status", fake_status)
    monkeypatch.setattr(main.printer, "health", exploding_health)

    response = logged_in.get("/settings")
    assert response.status_code == 200
    assert 'hx-trigger="load, every 10s"' in response.text  # asks right away
    assert "status-dot pending" in response.text


def test_settings_page_waits_for_neither_connection(logged_in, monkeypatch):
    """Both checks talk to hosts that may be down, and a dead host answers with
    nothing — the page must not sit out either timeout."""
    import app.main as main

    async def exploding_status():
        raise AssertionError("settings page must not block on Homebox")

    async def exploding_health():
        raise AssertionError("settings page must not block on the print agent")

    monkeypatch.setattr(main.homebox, "status", exploding_status)
    monkeypatch.setattr(main.printer, "health", exploding_health)

    response = logged_in.get("/settings")
    assert response.status_code == 200
    assert response.text.count("status-dot pending") == 2
    assert 'hx-get="/settings/homebox-status"' in response.text


def test_homebox_status_fragment_keeps_polling_when_connected(logged_in, monkeypatch):
    """A green row that stops asking shows a state that is minutes old. The
    check reuses the cached token, so it is cheap enough to keep running."""
    import app.main as main

    async def fake_status():
        return None

    monkeypatch.setattr(main.homebox, "status", fake_status)
    response = logged_in.get("/settings/homebox-status")
    assert response.status_code == 200
    assert "status-dot ok" in response.text
    assert 'hx-trigger="every 30s"' in response.text
    assert response.headers["cache-control"] == "no-store"


def test_homebox_status_fragment_retries_while_broken(logged_in, monkeypatch):
    import app.main as main

    async def failing_status():
        raise main.HomeboxError("Homebox unreachable: timed out")

    monkeypatch.setattr(main.homebox, "status", failing_status)
    response = logged_in.get("/settings/homebox-status")
    assert "status-dot err" in response.text
    assert "timed out" in response.text
    assert 'hx-trigger="every 30s"' in response.text  # recovers on its own


def test_homebox_status_fragment_requires_login(client):
    response = client.get("/settings/homebox-status", headers={"HX-Request": "true"})
    assert response.status_code == 401


def test_agent_row_clears_the_shutdown_note_once_the_pi_is_down(logged_in, monkeypatch):
    """Otherwise "Pi is shutting down" is still on screen next to a Pi that is
    long back up — the note is only true until the Pi is actually gone."""
    import app.main as main

    async def down():
        return {"ok": False, "error": "Connection refused"}

    async def up():
        return {"ok": True, "dry_run": False}

    monkeypatch.setattr(main.printer, "health", down)
    gone = logged_in.get("/settings/agent-status")
    assert 'id="shutdown-status" hx-swap-oob="true"' in gone.text

    monkeypatch.setattr(main.printer, "health", up)
    alive = logged_in.get("/settings/agent-status")
    # still shutting down: the agent answers, so the note has to stay
    assert "hx-swap-oob" not in alive.text


def test_agent_controls_are_hidden_while_the_pi_does_not_answer(logged_in, monkeypatch):
    """Test print and shutdown can only fail while the agent is unreachable."""
    import app.main as main

    async def down():
        return {"ok": False, "error": "Connection refused"}

    async def up():
        return {"ok": True, "dry_run": False}

    monkeypatch.setattr(main.printer, "health", down)
    assert "agent-offline" in logged_in.get("/settings/agent-status").text

    monkeypatch.setattr(main.printer, "health", up)
    assert "agent-offline" not in logged_in.get("/settings/agent-status").text

    # unknown state on page load counts as offline — no flash of dead buttons
    async def exploding():
        raise AssertionError("page must not block on the agent")

    async def fake_status():
        return None

    monkeypatch.setattr(main.printer, "health", exploding)
    monkeypatch.setattr(main.homebox, "status", fake_status)
    page = logged_in.get("/settings").text
    assert 'class="status-row agent-offline"' in page
    assert page.count("agent-actions") == 2  # button row + hint follow the rule


def test_last_location_is_remembered_and_preselected_for_every_item(logged_in, monkeypatch):
    """Most orders go to one and the same place, so the location last used is
    pre-selected — on every card, not just the first."""
    import app.main as main
    from app import prefs
    from app.models import Order, OrderItemDraft, Shop

    async def fake_create_item(draft, order, location_id, label_ids):
        return {"id": "item1", "assetId": "000-007"}

    monkeypatch.setattr(main.homebox, "create_item", fake_create_item)
    logged_in.post("/create-item", data={
        "idx": "0", "shop": "amazon", "order_no": "028-111", "order_date": "",
        "item_count": "1", "item-0-name": "USB Hub", "item-0-quantity": "1",
        "item-0-location": "loc2",
    })
    assert prefs.get_last_location_id() == "loc2"

    async def fake_locations():
        return [{"id": "loc1", "name": "Büro"}, {"id": "loc2", "name": "Werkstatt"}]

    async def fake_labels():
        return []

    class TwoItemScraper:
        async def fetch_order(self, order_no):
            return Order(
                shop=Shop.amazon, order_no=order_no, order_date="",
                items=[OrderItemDraft(name="A", quantity=1),
                       OrderItemDraft(name="B", quantity=1)],
            )

    monkeypatch.setattr(main.homebox, "get_locations", fake_locations)
    monkeypatch.setattr(main.homebox, "get_labels", fake_labels)
    monkeypatch.setattr(main, "get_scraper", lambda shop: TwoItemScraper())

    page = logged_in.post(
        "/fetch", data={"shop": "amazon", "order_no": "028-1674448-8402738"}
    ).text
    assert page.count('value="loc2" selected') == 2
    assert 'value="loc1" selected' not in page


def test_each_card_offers_applying_its_location_to_all_cards(logged_in, monkeypatch):
    """The button copies this card's location to the others (app.js,
    applyLocationToAllCards). It is hidden until a second card exists, and it
    finds the selects by their loc-select-{idx} id."""
    import app.main as main
    from app.models import Order, OrderItemDraft, Shop

    async def fake_locations():
        return [{"id": "loc1", "name": "Büro"}, {"id": "loc2", "name": "Werkstatt"}]

    async def fake_labels():
        return []

    class TwoItemScraper:
        async def fetch_order(self, order_no):
            return Order(
                shop=Shop.amazon, order_no=order_no, order_date="",
                items=[OrderItemDraft(name="A", quantity=1),
                       OrderItemDraft(name="B", quantity=1)],
            )

    monkeypatch.setattr(main.homebox, "get_locations", fake_locations)
    monkeypatch.setattr(main.homebox, "get_labels", fake_labels)
    monkeypatch.setattr(main, "get_scraper", lambda shop: TwoItemScraper())

    page = logged_in.post(
        "/fetch", data={"shop": "amazon", "order_no": "028-1674448-8402738"}
    ).text
    assert page.count("applyLocationToAllCards(") == 2
    assert 'id="loc-select-0"' in page and 'id="loc-select-1"' in page
    # rendered hidden; app.js reveals it once a second card is there
    assert page.count('class="btn btn-ghost btn-small apply-all hidden"') == 2
    assert "Auf alle Karten übernehmen" in page  # from the locale, German default

    script = (main.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    assert "function applyLocationToAllCards" in script
    assert "function updateApplyAllButtons" in script


def test_apply_location_strings_exist_in_both_languages():
    import json

    from app.main import BASE_DIR

    for lang in ("de", "en"):
        strings = json.loads((BASE_DIR / "locales" / f"{lang}.json").read_text("utf-8"))
        assert strings["apply_location_all"]
        assert strings["apply_location_done"]


def test_a_failed_creation_does_not_change_the_remembered_location(logged_in, monkeypatch):
    import app.main as main
    from app import prefs
    from app.homebox import HomeboxError

    prefs.set_last_location_id("loc-good")

    async def failing_create(draft, order, location_id, label_ids):
        raise HomeboxError("nope")

    async def fake_empty():
        return []

    monkeypatch.setattr(main.homebox, "create_item", failing_create)
    monkeypatch.setattr(main.homebox, "get_locations", fake_empty)
    monkeypatch.setattr(main.homebox, "get_labels", fake_empty)

    logged_in.post("/create-item", data={
        "idx": "0", "shop": "amazon", "order_no": "", "order_date": "",
        "item_count": "1", "item-0-name": "Thing", "item-0-quantity": "1",
        "item-0-location": "loc-bad",
    })
    assert prefs.get_last_location_id() == "loc-good"


def test_prefs_survive_a_corrupt_file(tmp_path, monkeypatch):
    """A broken preference file must never take the app down with it."""
    from app import prefs
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "prefs.json").write_text("{not json", encoding="utf-8")
    assert prefs.get_last_location_id() == ""
    prefs.set_last_location_id("loc9")
    assert prefs.get_last_location_id() == "loc9"
