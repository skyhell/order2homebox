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
