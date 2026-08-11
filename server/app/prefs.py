"""Small persisted UI preferences (data/prefs.json).

Only things that should survive a restart and are not worth a database — the
location last used, which is pre-selected for every item of the next order
(most orders end up in one and the same place), the text labels last printed,
which are usually printed again, and what was typed into the input fields that
get the same values over and over (order number, asset ID, the two text lines).
"""
import json
from pathlib import Path

from .config import settings

LAST_LOCATION = "last_location_id"
TEXT_LABELS = "text_labels"
TEXT_LABELS_MAX = 8  # enough to find a repeat, short enough to stay scannable
HISTORY_MAX = TEXT_LABELS_MAX  # same for every field history
TEXT_HEIGHT_MODE = "text_height_mode"
TEXT_KEEP_HEIGHT = "text_keep_height"  # superseded by the above; still read
ORDERS = "orders"  # [{"shop": …, "order_no": …}] — the shop belongs to the number
ASSET_REFS = "asset_refs"  # resolved asset IDs, not the pasted links
TEXT_LINES = ("text_line1_history", "text_line2_history")


def _path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "prefs.json"


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # missing or corrupt — a preference is never worth an error
    return data if isinstance(data, dict) else {}


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value)


def _is_order(entry) -> bool:
    return (
        isinstance(entry, dict)
        and _is_text(entry.get("shop"))
        and _is_text(entry.get("order_no"))
    )


def _history(key: str, keep) -> list:
    """One field's history, newest first — anything unreadable is dropped."""
    entries = _read().get(key)
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if keep(entry)][:HISTORY_MAX]


def _push(data: dict, key: str, entry, keep) -> None:
    """Put an entry at the front of its history.

    A repeat moves back to the front instead of being stored twice, so the list
    stays distinct and the value used most recently is always first. Takes the
    dict to write into so several histories can share one _write().
    """
    old = data.get(key)
    old = [e for e in old if keep(e) and e != entry] if isinstance(old, list) else []
    data[key] = [entry] + old[: HISTORY_MAX - 1]


def get_last_location_id() -> str:
    value = _read().get(LAST_LOCATION, "")
    return value if isinstance(value, str) else ""


def set_last_location_id(location_id: str) -> None:
    """Remember the location an item was actually created in."""
    if not location_id:
        return
    data = _read()
    if data.get(LAST_LOCATION) == location_id:
        return
    data[LAST_LOCATION] = location_id
    _write(data)


def get_orders() -> list[dict]:
    """Order numbers last fetched, newest first — each with the shop it belongs
    to, because the same number means nothing without it."""
    return _history(ORDERS, _is_order)


def remember_order(shop: str, order_no: str) -> None:
    """Remember an order number a shop page really answered for."""
    if not shop or not order_no:
        return
    data = _read()
    _push(data, ORDERS, {"shop": shop, "order_no": order_no}, _is_order)
    _write(data)


def get_asset_refs() -> list[str]:
    """Asset IDs last resolved on the reprint page, newest first."""
    return _history(ASSET_REFS, _is_text)


def remember_asset_ref(asset_id: str) -> None:
    """Remember the resolved ID, not what was pasted: it is short, unambiguous
    and resolves again in one step."""
    if not asset_id:
        return
    data = _read()
    _push(data, ASSET_REFS, asset_id, _is_text)
    _write(data)


def get_text_line_history(index: int) -> list[str]:
    """What line 1 (index 0) or line 2 (index 1) was last printed with."""
    return _history(TEXT_LINES[index], _is_text)


def get_text_labels() -> list[list[str]]:
    """Text labels last printed, newest first — each one or two lines."""
    entries = _read().get(TEXT_LABELS)
    if not isinstance(entries, list):
        return []
    clean = [
        entry[:2]
        for entry in entries
        if isinstance(entry, list)
        and entry
        and all(isinstance(line, str) and line for line in entry)
    ]
    return clean[:TEXT_LABELS_MAX]


def get_text_height_mode() -> str:
    """Which height mode the text page starts in (see labels.HEIGHT_*)."""
    from .labels import HEIGHT_GROW, HEIGHT_KEEP, HEIGHT_MODES

    data = _read()
    mode = data.get(TEXT_HEIGHT_MODE)
    if mode in HEIGHT_MODES:
        return mode
    # Written by the version that only had the two-state checkbox.
    return HEIGHT_KEEP if data.get(TEXT_KEEP_HEIGHT) is True else HEIGHT_GROW


def remember_text_label(lines: list[str], height_mode: str = "") -> None:
    """Remember a text label that was really printed.

    Keeps two things at once, in one write: the whole label (the chips, which
    reprint it with a single click) and each line on its own (the field
    histories, which let one line be combined with a new second one).
    """
    lines = [line for line in lines if line]
    if not lines:
        return
    data = _read()
    _push(data, TEXT_LABELS, lines, lambda e: isinstance(e, list) and bool(e))
    for index, line in enumerate(lines[:2]):
        _push(data, TEXT_LINES[index], line, _is_text)
    # The mode travels with the print, not with the checkbox: the page should
    # come back the way the last label was actually made.
    from .labels import HEIGHT_GROW, HEIGHT_MODES

    data[TEXT_HEIGHT_MODE] = height_mode if height_mode in HEIGHT_MODES else HEIGHT_GROW
    data.pop(TEXT_KEEP_HEIGHT, None)  # the old two-state flag is now ambiguous
    _write(data)


def _write(data: dict) -> None:
    try:
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # losing a preference must never break printing or creating
