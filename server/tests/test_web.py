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
