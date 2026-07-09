"""Async client for the Homebox API (login, locations, labels, items)."""
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

    # -- API --------------------------------------------------------------

    async def status(self) -> dict:
        """Connection test used on the settings page."""
        await self._login()
        return {"ok": True, "url": self.base_url}

    async def get_locations(self) -> list[dict]:
        r = await self._request("GET", "/api/v1/locations")
        locations = self._ok(r, "list locations") or []
        return sorted(locations, key=lambda loc: loc.get("name", "").lower())

    async def create_location(self, name: str, description: str = "") -> dict:
        r = await self._request(
            "POST",
            "/api/v1/locations",
            json={"name": name, "description": description},
        )
        return self._ok(r, "create location")

    async def get_labels(self) -> list[dict]:
        r = await self._request("GET", "/api/v1/labels")
        labels = self._ok(r, "list labels") or []
        return sorted(labels, key=lambda lb: lb.get("name", "").lower())

    async def get_item(self, item_id: str) -> dict:
        r = await self._request("GET", f"/api/v1/items/{item_id}")
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
        notes = []
        if order.order_no:
            notes.append(f"Order: {order.order_no}")
        if draft.product_url:
            notes.append(draft.product_url)
        update["notes"] = "\n".join(notes)
        fields = list(update.get("fields") or [])
        if order.order_no:
            fields.append({"name": "Order Number", "type": "text", "textValue": order.order_no})
        update["fields"] = fields

        r = await self._request("PUT", f"/api/v1/items/{item['id']}", json=update)
        if r.status_code not in (200, 201):
            # Some Homebox versions are picky about custom fields — retry without them.
            update.pop("fields", None)
            r = await self._request("PUT", f"/api/v1/items/{item['id']}", json=update)
        self._ok(r, "update item")
        return await self.get_item(item["id"])

    @staticmethod
    def _item_to_update(item: dict) -> dict:
        """Convert a GET /items/{id} response into a PUT payload, preserving
        server-side values and flattening nested location/labels objects."""
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

    def item_url(self, item: dict) -> str:
        return f"{settings.qr_base_url}/item/{item.get('id', '')}"

    @staticmethod
    def asset_qr_url(asset_id: str) -> str:
        return f"{settings.qr_base_url}/a/{asset_id}"
