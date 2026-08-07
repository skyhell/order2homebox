"""Small persisted UI preferences (data/prefs.json).

Only things that should survive a restart and are not worth a database — the
location last used, which is pre-selected for every item of the next order
(most orders end up in one and the same place), and the text labels last
printed, which are usually printed again.
"""
import json
from pathlib import Path

from .config import settings

LAST_LOCATION = "last_location_id"
TEXT_LABELS = "text_labels"
TEXT_LABELS_MAX = 8  # enough to find a repeat, short enough to stay scannable
TEXT_KEEP_HEIGHT = "text_keep_height"


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
    _write(data)


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


def get_text_keep_height() -> bool:
    """Whether the text page starts with 'keep the label height' ticked."""
    return _read().get(TEXT_KEEP_HEIGHT) is True


def remember_text_label(lines: list[str], keep_height: bool = False) -> None:
    """Remember a text label that was really printed.

    Moves a repeat back to the front instead of storing it twice, so the list
    stays a list of distinct labels and the one used most recently is first.
    """
    lines = [line for line in lines if line]
    if not lines:
        return
    history = [entry for entry in get_text_labels() if entry != lines]
    data = _read()
    data[TEXT_LABELS] = [lines] + history[: TEXT_LABELS_MAX - 1]
    # The mode travels with the print, not with the checkbox: the page should
    # come back the way the last label was actually made.
    data[TEXT_KEEP_HEIGHT] = bool(keep_height)
    _write(data)


def _write(data: dict) -> None:
    try:
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # losing a preference must never break printing or creating
