import json

import pytest
import respx
from httpx import Response

from app.homebox import HomeboxClient, HomeboxError
from app.models import Order, OrderItemDraft, Shop

BASE = "http://homebox.test"

LOGIN_OK = {"token": "Bearer test-token", "expiresAt": "2099-01-01T00:00:00Z"}


def mock_login(respx_mock):
    return respx_mock.post("/api/v1/users/login").mock(
        return_value=Response(200, json=LOGIN_OK)
    )


@pytest.fixture()
def hb():
    return HomeboxClient(base_url=BASE, username="u", password="p")


@respx.mock(base_url=BASE)
async def test_login_and_get_locations(respx_mock, hb):
    mock_login(respx_mock)
    respx_mock.get("/api/v1/locations").mock(
        return_value=Response(
            200,
            json=[
                {"id": "loc2", "name": "Werkstatt"},
                {"id": "loc1", "name": "Büro"},
            ],
        )
    )
    locations = await hb.get_locations()
    assert [loc["name"] for loc in locations] == ["Büro", "Werkstatt"]
    await hb.close()


@respx.mock(base_url=BASE)
async def test_login_failure_raises(respx_mock, hb):
    respx_mock.post("/api/v1/users/login").mock(return_value=Response(401))
    with pytest.raises(HomeboxError):
        await hb.get_locations()
    await hb.close()


@respx.mock(base_url=BASE)
async def test_create_location(respx_mock, hb):
    mock_login(respx_mock)
    route = respx_mock.post("/api/v1/locations").mock(
        return_value=Response(201, json={"id": "locNew", "name": "Regal A"})
    )
    created = await hb.create_location("Regal A")
    assert created["id"] == "locNew"
    body = json.loads(route.calls.last.request.content)
    assert body["name"] == "Regal A"
    await hb.close()


@respx.mock(base_url=BASE)
async def test_create_item_full_flow(respx_mock, hb):
    mock_login(respx_mock)
    respx_mock.post("/api/v1/items").mock(
        return_value=Response(201, json={"id": "item1"})
    )
    item_state = {
        "id": "item1",
        "name": "USB-C Hub",
        "description": "",
        "quantity": 1,
        "assetId": "000-123",
        "insured": False,
        "archived": False,
        "location": {"id": "loc1", "name": "Büro"},
        "labels": [{"id": "lab1", "name": "Elektronik"}],
        "fields": [],
    }
    respx_mock.get("/api/v1/items/item1").mock(return_value=Response(200, json=item_state))
    put_route = respx_mock.put("/api/v1/items/item1").mock(
        return_value=Response(200, json=item_state)
    )

    order = Order(shop=Shop.amazon, order_no="302-111", order_date="2026-07-03")
    draft = OrderItemDraft(name="USB-C Hub", quantity=2, unit_price=24.99,
                           product_url="https://www.amazon.de/dp/B0TEST123")
    item = await hb.create_item(draft, order, "loc1", ["lab1"])

    assert item["assetId"] == "000-123"
    update = json.loads(put_route.calls.last.request.content)
    assert update["quantity"] == 2
    assert update["purchaseFrom"] == "Amazon"
    assert update["purchasePrice"] == 24.99
    assert update["purchaseTime"] == "2026-07-03"
    assert update["locationId"] == "loc1"
    assert update["labelIds"] == ["lab1"]
    assert "302-111" in update["notes"]
    assert any(f.get("textValue") == "302-111" for f in update["fields"])
    await hb.close()


@respx.mock(base_url=BASE)
async def test_relogin_on_401(respx_mock, hb):
    login_route = mock_login(respx_mock)
    respx_mock.get("/api/v1/labels").mock(
        side_effect=[Response(401), Response(200, json=[])]
    )
    labels = await hb.get_labels()
    assert labels == []
    assert login_route.call_count == 2
    await hb.close()


def test_asset_qr_url():
    assert HomeboxClient.asset_qr_url("000-123") == "http://homebox.test/a/000-123"
