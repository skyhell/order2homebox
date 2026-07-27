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
    assert "labelPreview('preview-1', '000-007', this.checked)" in body


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


def test_homebox_status_fragment_does_not_poll_when_connected(logged_in, monkeypatch):
    """Every check costs a full login, so a healthy row must stay quiet."""
    import app.main as main

    async def fake_status():
        return None

    monkeypatch.setattr(main.homebox, "status", fake_status)
    response = logged_in.get("/settings/homebox-status")
    assert response.status_code == 200
    assert "status-dot ok" in response.text
    assert "hx-trigger" not in response.text
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
