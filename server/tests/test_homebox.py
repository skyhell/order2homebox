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


def mock_legacy_locations(respx_mock, locations=None):
    """Legacy probe: GET /api/v1/locations answers 200."""
    return respx_mock.get("/api/v1/locations").mock(
        return_value=Response(200, json=locations or [])
    )


def mock_entities_probe(respx_mock):
    """Entities probe: GET /api/v1/locations answers 404 (new API)."""
    return respx_mock.get("/api/v1/locations").mock(
        return_value=Response(404, text="404 page not found")
    )


@pytest.fixture()
def hb():
    return HomeboxClient(base_url=BASE, username="u", password="p")


# -- legacy API (Homebox <= 0.x) ------------------------------------------------


@respx.mock(base_url=BASE)
async def test_legacy_login_and_get_locations(respx_mock, hb):
    mock_login(respx_mock)
    mock_legacy_locations(
        respx_mock,
        [{"id": "loc2", "name": "Werkstatt"}, {"id": "loc1", "name": "Büro"}],
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
async def test_legacy_create_location(respx_mock, hb):
    mock_login(respx_mock)
    mock_legacy_locations(respx_mock)
    route = respx_mock.post("/api/v1/locations").mock(
        return_value=Response(201, json={"id": "locNew", "name": "Regal A"})
    )
    created = await hb.create_location("Regal A")
    assert created["id"] == "locNew"
    body = json.loads(route.calls.last.request.content)
    assert body["name"] == "Regal A"
    await hb.close()


@respx.mock(base_url=BASE)
async def test_legacy_create_item_full_flow(respx_mock, hb):
    mock_login(respx_mock)
    mock_legacy_locations(respx_mock)
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
    mock_legacy_locations(respx_mock)
    respx_mock.get("/api/v1/labels").mock(
        side_effect=[Response(401), Response(200, json=[])]
    )
    labels = await hb.get_labels()
    assert labels == []
    assert login_route.call_count == 2
    await hb.close()


# -- entities API (newer Homebox: /entities, /tags) --------------------------------


@respx.mock(base_url=BASE)
async def test_entities_get_locations(respx_mock, hb):
    mock_login(respx_mock)
    mock_entities_probe(respx_mock)
    route = respx_mock.get("/api/v1/entities").mock(
        return_value=Response(
            200,
            json={
                "items": [
                    {"id": "e2", "name": "Werkstatt"},
                    {"id": "e1", "name": "Büro"},
                ],
                "total": 2,
            },
        )
    )
    locations = await hb.get_locations()
    assert [loc["name"] for loc in locations] == ["Büro", "Werkstatt"]
    params = dict(route.calls.last.request.url.params)
    assert params["isLocation"] == "true"
    await hb.close()


@respx.mock(base_url=BASE)
async def test_entities_get_labels_uses_tags(respx_mock, hb):
    mock_login(respx_mock)
    mock_entities_probe(respx_mock)
    respx_mock.get("/api/v1/tags").mock(
        return_value=Response(200, json=[{"id": "t1", "name": "Elektronik"}])
    )
    labels = await hb.get_labels()
    assert labels == [{"id": "t1", "name": "Elektronik"}]
    await hb.close()


@respx.mock(base_url=BASE)
async def test_entities_create_location_resolves_type(respx_mock, hb):
    mock_login(respx_mock)
    mock_entities_probe(respx_mock)
    respx_mock.get("/api/v1/entity-types").mock(
        return_value=Response(
            200,
            json=[
                {"id": "type-item", "name": "Item", "isLocation": False},
                {"id": "type-loc", "name": "Location", "isLocation": True},
            ],
        )
    )
    route = respx_mock.post("/api/v1/entities").mock(
        return_value=Response(201, json={"id": "eNew", "name": "Regal A"})
    )
    created = await hb.create_location("Regal A")
    assert created["id"] == "eNew"
    body = json.loads(route.calls.last.request.content)
    assert body["name"] == "Regal A"
    assert body["entityTypeId"] == "type-loc"
    await hb.close()


@respx.mock(base_url=BASE)
async def test_entities_create_item_full_flow(respx_mock, hb):
    mock_login(respx_mock)
    mock_entities_probe(respx_mock)
    post_route = respx_mock.post("/api/v1/entities").mock(
        return_value=Response(201, json={"id": "ent1"})
    )
    entity_state = {
        "id": "ent1",
        "name": "USB-C Hub",
        "description": "",
        "quantity": 1,
        "assetId": "000-042",
        "insured": False,
        "archived": False,
        "syncChildEntityLocations": False,
        "parent": {"id": "loc1", "name": "Büro"},
        "entityType": {"id": "type-item", "name": "Item", "isLocation": False},
        "tags": [{"id": "t1", "name": "Elektronik"}],
        "purchaseDate": "0001-01-01T00:00:00Z",
        "fields": [],
    }
    respx_mock.get("/api/v1/entities/ent1").mock(
        return_value=Response(200, json=entity_state)
    )
    put_route = respx_mock.put("/api/v1/entities/ent1").mock(
        return_value=Response(200, json=entity_state)
    )

    order = Order(shop=Shop.temu, order_no="PO-211-999", order_date="2026-07-03")
    draft = OrderItemDraft(name="USB-C Hub", quantity=3, unit_price=9.99)
    item = await hb.create_item(draft, order, "loc1", ["t1"])

    assert item["assetId"] == "000-042"
    create_body = json.loads(post_route.calls.last.request.content)
    assert create_body["parentId"] == "loc1"
    assert create_body["tagIds"] == ["t1"]
    assert "entityTypeId" not in create_body  # Homebox resolves default Item type

    update = json.loads(put_route.calls.last.request.content)
    assert update["quantity"] == 3
    assert update["purchaseFrom"] == "Temu"
    assert update["purchasePrice"] == 9.99
    assert update["purchaseDate"] == "2026-07-03"
    assert update["parentId"] == "loc1"
    assert update["entityTypeId"] == "type-item"
    assert update["tagIds"] == ["t1"]
    assert "PO-211-999" in update["notes"]
    assert any(f.get("textValue") == "PO-211-999" for f in update["fields"])
    await hb.close()


def test_asset_qr_url():
    assert HomeboxClient.asset_qr_url("000-123") == "http://homebox.test/a/000-123"
