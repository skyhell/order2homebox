"""The order currently being edited (data/draft.json).

The edit page only ever existed as the answer to POST /fetch — it had no address
of its own and nothing was kept, so every click on another page threw the fetched
order away. What is kept here is the raw form of #create-form, not a second data
model: _item_from_form() in main.py already turns exactly those fields into a
draft, a location, labels and the three checkboxes, and restoring reuses it
rather than growing a second parser that can drift.

Replaced by the next fetch, never expired on its own.
"""
import json
from pathlib import Path

from .config import settings

META_FIELDS = ("shop", "order_no", "order_date")


class StoredForm(dict):
    """A saved form, read like a Starlette FormData — same getlist() so
    _item_from_form() works on it unchanged (labels are a multi-select)."""

    def getlist(self, key: str) -> list:
        value = self.get(key)
        if isinstance(value, list):
            return value
        return [value] if value not in (None, "") else []


def _path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "draft.json"


def load() -> dict | None:
    """The stored draft, or None — an unreadable file is simply no draft."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("fields"), dict):
        return None
    return data


def clear() -> None:
    """Drop the draft — the next fetch replaces what is on the page."""
    try:
        _path().unlink()
    except OSError:
        pass


def form(data: dict) -> StoredForm:
    """The stored fields as something _item_from_form() can read."""
    stored = StoredForm(data.get("fields", {}))
    for key in META_FIELDS:
        stored.setdefault(key, data.get(key, ""))
    return stored


def created_items(data: dict) -> dict:
    entries = data.get("created")
    return entries if isinstance(entries, dict) else {}


def save(posted) -> None:
    """Store the edit form as it currently stands.

    Keeps the items already created: a delayed auto-save (the beacon sent while
    the page is going away) must never turn a result card back into an input
    card — the item would be created a second time.
    """
    fields = {}
    for key in posted.keys():
        values = [str(value) for value in posted.getlist(key)]
        if not values:
            continue
        fields[key] = values if len(values) > 1 else values[0]
    try:
        count = int(fields.get("item_count", 0))
    except (TypeError, ValueError):
        count = 0
    previous = load() or {}
    data = {
        "item_count": count,
        "fields": fields,
        "created": created_items(previous) if _same_order(previous, fields) else {},
    }
    for key in META_FIELDS:
        data[key] = str(fields.get(key, ""))
    _write(data)


def _same_order(previous: dict, fields: dict) -> bool:
    """Whether a stored draft describes the order this form belongs to. A new
    fetch clears the file, so this only guards against a beacon from the page
    that was just left behind."""
    if not previous:
        return False
    return all(str(previous.get(key, "")) == str(fields.get(key, "")) for key in ("shop", "order_no"))


def mark_created(idx: int, entry: dict) -> None:
    """Remember that this card's item is in Homebox, with what the result card
    needs to come back: asset ID, item ID and what was printed."""
    data = load()
    item = entry.get("item") or {}
    if not data or not item.get("assetId"):
        return
    created = created_items(data)
    created[str(idx)] = {
        "name": entry["draft"].name,
        "asset_id": item.get("assetId", ""),
        "item_id": item.get("id", ""),
        "printed": bool(entry.get("printed")),
        "print_error": entry.get("print_error", ""),
        "show_asset_id": bool(entry.get("show_asset_id")),
        "qr_per_row": int(entry.get("qr_per_row") or 2),
    }
    data["created"] = created
    _write(data)


def update_print_result(idx: int, asset_id: str, printed: bool, error: str) -> None:
    """Overwrite what a created card remembers about printing, so a reprint that
    worked also survives a reload of the edit page — the failed attempt from the
    moment the item was created is not the last word on it.

    The asset ID is the guard: a card index travelling with a page that has since
    been replaced must not write onto whatever item sits at that index now.
    """
    data = load()
    if not data or idx < 0 or not asset_id:
        return
    created = created_items(data)
    entry = created.get(str(idx))
    if not entry or entry.get("asset_id") != asset_id:
        return
    entry["printed"] = printed
    entry["print_error"] = error
    data["created"] = created
    _write(data)


def summary() -> dict | None:
    """What the nav link needs: order number, shop and how many cards — None
    while there is no draft, and then no link is shown at all."""
    data = load()
    if not data:
        return None
    fields = data.get("fields", {})
    cards = sum(
        1
        for i in range(data.get("item_count", 0))
        if f"item-{i}-name" in fields or str(i) in created_items(data)
    )
    if not cards:
        return None
    return {
        "order_no": str(data.get("order_no", "")),
        "shop": str(data.get("shop", "")),
        "cards": cards,
    }


def _write(data: dict) -> None:
    try:
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # losing the draft must never break creating or printing
