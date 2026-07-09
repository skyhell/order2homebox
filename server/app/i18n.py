"""Tiny JSON-file based i18n. UI strings live in locales/{de,en}.json only."""
import json
from pathlib import Path

from fastapi import Request

from .config import settings

LOCALES_DIR = Path(__file__).parent / "locales"
LANG_COOKIE = "o2h_lang"

_translations: dict[str, dict[str, str]] = {}


def load_translations() -> None:
    _translations.clear()
    for path in sorted(LOCALES_DIR.glob("*.json")):
        _translations[path.stem] = json.loads(path.read_text(encoding="utf-8"))


def available_languages() -> list[str]:
    return sorted(_translations)


def t(key: str, lang: str, **kwargs) -> str:
    text = (
        _translations.get(lang, {}).get(key)
        or _translations.get("en", {}).get(key)
        or key
    )
    return text.format(**kwargs) if kwargs else text


def get_lang(request: Request) -> str:
    lang = request.cookies.get(LANG_COOKIE, "")
    return lang if lang in _translations else settings.default_language
