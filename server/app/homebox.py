"""Async client for the Homebox API.

Supports both API generations and detects the right one at runtime:

- **legacy** (Homebox ≤ 0.x): ``/api/v1/locations``, ``/api/v1/labels``,
  ``/api/v1/items``
- **entities** (newer Homebox): everything is an entity — locations are
  entities whose type has ``isLocation``; labels became ``/api/v1/tags``;
  items/locations live under ``/api/v1/entities``.

Detection: one probe of ``GET /api/v1/locations`` — 404 means entities API.
Normalized return values: locations/labels as ``{id, name}`` dicts, items as
dicts that always carry ``id`` and ``assetId``.
"""
import time
from datetime import datetime
from typing import Any

import httpx

from .config import settings
from .models import Order, OrderItemDraft


class HomeboxError(Exception):
    """User-facing Homebox API error."""


class HomeboxClient:
    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.homebox_url).rstrip("/")
        self.username = username if username is not None else settings.homebox_username
        self.password = password if password is not None else settings.homebox_password
        self._token: str | None = None
        self._token_expires: float = 0.0
        self._client: httpx.AsyncClient | None = None
        self._mode: str | None = None  # "legacy" | "entities"
        self._location_type: str | None = None  # entities mode: cached type id

    # -- plumbing ---------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=15.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _login(self) -> None:
        try:
            r = await self._http().post(
                "/api/v1/users/login",
                json={
                    "username": self.username,
                    "password": self.password,
                    "stayLoggedIn": True,
                },
            )
        except httpx.HTTPError as exc:
            raise HomeboxError(f"Homebox unreachable: {exc}") from exc
        if r.status_code != 200:
            raise HomeboxError(f"Homebox login failed (HTTP {r.status_code})")
        data = r.json()
        token = data.get("token", "")
        if not token:
            raise HomeboxError("Homebox login returned no token")
        self._token = token if token.startswith("Bearer") else f"Bearer {token}"
        expires_at = data.get("expiresAt", "")
        try:
            dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            self._token_expires = dt.timestamp()
        except (ValueError, AttributeError):
            self._token_expires = time.time() + 1800

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self._token or time.time() > self._token_expires - 60:
            await self._login()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = self._token
        try:
            r = await self._http().request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise HomeboxError(f"Homebox unreachable: {exc}") from exc
        if r.status_code == 401:
            # Token was revoked/expired early — re-login once and retry.
            await self._login()
            headers["Authorization"] = self._token
            r = await self._http().request(method, path, headers=headers, **kwargs)
        return r

    @staticmethod
    def _ok(r: httpx.Response, what: str) -> Any:
        if r.status_code not in (200, 201):
            detail = r.text[:200]
            raise HomeboxError(f"Homebox: {what} failed (HTTP {r.status_code}): {detail}")
        return r.json() if r.content else None

    async def _ensure_mode(self) -> str:
        """Detect the API generation once (probe /api/v1/locations)."""
        if self._mode is None:
            r = await self._request("GET", "/api/v1/locations")
            if r.status_code == 404:
                self._mode = "entities"
            elif r.status_code == 200:
                self._mode = "legacy"
            else:
                raise HomeboxError(
                    f"Homebox: unexpected response while probing the API "
                    f"(HTTP {r.status_code})"
                )
        return self._mode

    # -- API --------------------------------------------------------------

    async def status(self) -> dict:
        """Connection test used on the settings page."""
        await self._login()
        return {"ok": True, "url": self.base_url}

    async def get_locations(self) -> list[dict]:
        if await self._ensure_mode() == "legacy":
            r = await self._request("GET", "/api/v1/locations")
            locations = self._ok(r, "list locations") or []
        else:
            r = await self._request(
                "GET",
                "/api/v1/entities",
                params={"isLocation": "true", "pageSize": "500"},
            )
            locations = (self._ok(r, "list locations") or {}).get("items", [])
        return sorted(locations, key=lambda loc: loc.get("name", "").lower())

    async def _location_type_id(self) -> str:
        """entities mode: id of the entity type used for new locations."""
        if self._location_type is None:
            r = await self._request("GET", "/api/v1/entity-types")
            types = self._ok(r, "list entity types") or []
            location_types = [t for t in types if t.get("isLocation")]
            if not location_types:
                raise HomeboxError("Homebox: no location entity type found")
            preferred = [t for t in location_types if t.get("name") == "Location"]
            self._location_type = (preferred or location_types)[0]["id"]
        return self._location_type

    async def create_location(self, name: str, description: str = "") -> dict:
        if await self._ensure_mode() == "legacy":
            r = await self._request(
                "POST",
                "/api/v1/locations",
                json={"name": name, "description": description},
            )
        else:
            r = await self._request(
                "POST",
                "/api/v1/entities",
                json={
                    "name": name,
                    "description": description,
                    "entityTypeId": await self._location_type_id(),
                    "tagIds": [],
                },
            )
        return self._ok(r, "create location")

    async def get_labels(self) -> list[dict]:
        if await self._ensure_mode() == "legacy":
            r = await self._request("GET", "/api/v1/labels")
            labels = self._ok(r, "list labels") or []
        else:
            r = await self._request("GET", "/api/v1/tags")
            labels = self._ok(r, "list tags") or []
        return sorted(labels, key=lambda lb: lb.get("name", "").lower())

    async def get_item(self, item_id: str) -> dict:
        path = (
            f"/api/v1/items/{item_id}"
            if await self._ensure_mode() == "legacy"
            else f"/api/v1/entities/{item_id}"
        )
        r = await self._request("GET", path)
        return self._ok(r, "get item")

    async def create_item(
        self,
        draft: OrderItemDraft,
        order: Order,
        location_id: str,
        label_ids: list[str],
    ) -> dict:
        """Create an item and fill in purchase details; returns the final item
        including its ``assetId``."""
        if await self._ensure_mode() == "legacy":
            return await self._create_item_legacy(draft, order, location_id, label_ids)
        return await self._create_item_entities(draft, order, location_id, label_ids)

    # -- legacy flow --------------------------------------------------------

    async def _create_item_legacy(
        self, draft: OrderItemDraft, order: Order, location_id: str, label_ids: list[str]
    ) -> dict:
        r = await self._request(
            "POST",
            "/api/v1/items",
            json={
                "name": draft.name,
                "description": draft.description,
                "locationId": location_id,
                "labelIds": label_ids,
            },
        )
        created = self._ok(r, "create item")
        item = await self.get_item(created["id"])

        update = self._item_to_update(item)
        update["quantity"] = draft.quantity
        update["purchaseFrom"] = order.shop.display_name
        if draft.unit_price is not None:
            update["purchasePrice"] = draft.unit_price
        if order.order_date:
            update["purchaseTime"] = order.order_date
        update["notes"] = self._notes(draft, order)
        fields = list(update.get("fields") or [])
        fields.extend(self._order_fields(order))
        update["fields"] = fields

        r = await self._request("PUT", f"/api/v1/items/{item['id']}", json=update)
        if r.status_code not in (200, 201):
            # Some Homebox versions are picky about custom fields — retry without.
            update.pop("fields", None)
            update["notes"] = self._notes_with_order_no(update, order)
            r = await self._request("PUT", f"/api/v1/items/{item['id']}", json=update)
        self._ok(r, "update item")
        return await self.get_item(item["id"])

    @staticmethod
    def _item_to_update(item: dict) -> dict:
        """Convert a legacy GET /items/{id} response into a PUT payload."""
        update = {
            key: item.get(key)
            for key in (
                "id",
                "name",
                "description",
                "quantity",
                "assetId",
                "insured",
                "archived",
                "notes",
                "manufacturer",
                "modelNumber",
                "serialNumber",
                "lifetimeWarranty",
                "warrantyDetails",
                "purchaseFrom",
            )
            if item.get(key) is not None
        }
        location = item.get("location") or {}
        if location.get("id"):
            update["locationId"] = location["id"]
        update["labelIds"] = [lb["id"] for lb in item.get("labels") or []]
        update["fields"] = item.get("fields") or []
        return update

    # -- entities flow --------------------------------------------------------

    async def _create_item_entities(
        self, draft: OrderItemDraft, order: Order, location_id: str, label_ids: list[str]
    ) -> dict:
        # entityTypeId is omitted on purpose: Homebox auto-resolves the
        # group's default "Item" type for entities without an explicit type.
        r = await self._request(
            "POST",
            "/api/v1/entities",
            json={
                "name": draft.name,
                "description": draft.description,
                "quantity": draft.quantity,
                "parentId": location_id,
                "tagIds": label_ids,
            },
        )
        created = self._ok(r, "create item")
        item = await self.get_item(created["id"])

        update = self._entity_to_update(item)
        update["quantity"] = draft.quantity
        update["purchaseFrom"] = order.shop.display_name
        if draft.unit_price is not None:
            update["purchasePrice"] = draft.unit_price
        if order.order_date:
            update["purchaseDate"] = order.order_date
        update["notes"] = self._notes(draft, order)
        fields = list(update.get("fields") or [])
        fields.extend(self._order_fields(order))
        update["fields"] = fields

        r = await self._request("PUT", f"/api/v1/entities/{item['id']}", json=update)
        if r.status_code not in (200, 201):
            update.pop("fields", None)
            update["notes"] = self._notes_with_order_no(update, order)
            r = await self._request("PUT", f"/api/v1/entities/{item['id']}", json=update)
        self._ok(r, "update item")
        return await self.get_item(item["id"])

    @staticmethod
    def _entity_to_update(item: dict) -> dict:
        """Convert a GET /entities/{id} response into a PUT payload, flattening
        the parent/entityType/tags edges. Zero dates ("0001-01-01…") are
        dropped — echoing them back trips validation on some versions."""
        update = {
            key: item.get(key)
            for key in (
                "id",
                "name",
                "description",
                "quantity",
                "assetId",
                "insured",
                "archived",
                "syncChildEntityLocations",
                "notes",
                "manufacturer",
                "modelNumber",
                "serialNumber",
                "lifetimeWarranty",
                "warrantyDetails",
                "purchaseFrom",
            )
            if item.get(key) is not None
        }
        parent = item.get("parent") or {}
        if parent.get("id"):
            update["parentId"] = parent["id"]
        entity_type = item.get("entityType") or {}
        if entity_type.get("id"):
            update["entityTypeId"] = entity_type["id"]
        update["tagIds"] = [t["id"] for t in item.get("tags") or []]
        update["fields"] = item.get("fields") or []
        purchase_date = item.get("purchaseDate") or ""
        if purchase_date and not purchase_date.startswith("0001-01-01"):
            update["purchaseDate"] = purchase_date
        return update

    # -- shared helpers ---------------------------------------------------------

    @staticmethod
    def _notes(draft: OrderItemDraft, order: Order) -> str:
        # The order number lives in the "Order Number" custom field — notes
        # only carry the product URL to avoid duplication.
        return draft.product_url

    @staticmethod
    def _notes_with_order_no(update: dict, order: Order) -> str:
        """Fallback when custom fields are rejected: keep the order number in
        the notes so it isn't lost entirely."""
        parts = [f"Order: {order.order_no}"] if order.order_no else []
        if update.get("notes"):
            parts.append(update["notes"])
        return "\n".join(parts)

    @staticmethod
    def _order_fields(order: Order) -> list[dict]:
        if not order.order_no:
            return []
        return [{"name": "Order Number", "type": "text", "textValue": order.order_no}]

    def item_url(self, item: dict) -> str:
        return f"{settings.qr_base_url}/item/{item.get('id', '')}"

    @staticmethod
    def asset_qr_url(asset_id: str) -> str:
        return f"{settings.qr_base_url}/a/{asset_id}"
