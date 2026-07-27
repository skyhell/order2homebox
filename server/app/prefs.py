"""Small persisted UI preferences (data/prefs.json).

Only things that should survive a restart and are not worth a database —
currently the location last used, which is pre-selected for every item of the
next order (most orders end up in one and the same place).
"""
import json
from pathlib import Path

from .config import settings

LAST_LOCATION = "last_location_id"


def _path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "prefs.json"


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # missing or corrupt — a preference is never worth an error
    return data if isinstance(data, dict) else {}


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
    try:
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # losing the preference must never break creating items
