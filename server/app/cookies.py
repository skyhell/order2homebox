"""Storage for shop session cookies imported from a Cookie-Editor JSON export."""
import json
import os
import time
from datetime import datetime
from pathlib import Path

from .config import settings
from .models import Shop

# Cookie-Editor "sameSite" values → Playwright values
_SAME_SITE = {
    "no_restriction": "None",
    "none": "None",
    "lax": "Lax",
    "strict": "Strict",
}


class CookieError(Exception):
    """Invalid cookie import."""


def _cookies_dir() -> Path:
    path = settings.data_dir / "cookies"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cookies_path(shop: Shop) -> Path:
    return _cookies_dir() / f"{shop.value}.json"


def _meta_path(shop: Shop) -> Path:
    return _cookies_dir() / f"{shop.value}.meta.json"


def save_cookies(shop: Shop, raw_json: str) -> int:
    """Validate and store a Cookie-Editor JSON export. Returns cookie count."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise CookieError("not valid JSON") from exc
    if isinstance(data, dict):
        data = data.get("cookies", data)
    if not isinstance(data, list) or not data:
        raise CookieError("expected a JSON array of cookies")
    for cookie in data:
        if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie:
            raise CookieError("every cookie needs 'name' and 'value'")
    path = cookies_path(shop)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return len(data)


def load_playwright_cookies(shop: Shop) -> list[dict]:
    """Read the stored export and convert it to Playwright's cookie format.
    Returns [] when no cookies were imported yet."""
    path = cookies_path(shop)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    cookies = []
    for c in data:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", False)),
            "httpOnly": bool(c.get("httpOnly", False)),
        }
        expires = c.get("expirationDate") or c.get("expires")
        if expires:
            try:
                cookie["expires"] = float(expires)
            except (TypeError, ValueError):
                pass
        same_site = _SAME_SITE.get(str(c.get("sameSite", "")).lower())
        if same_site:
            cookie["sameSite"] = same_site
        if cookie["domain"]:
            cookies.append(cookie)
    return cookies


def record_success(shop: Shop) -> None:
    _meta_path(shop).write_text(
        json.dumps({"last_success": time.time()}), encoding="utf-8"
    )


def cookie_status(shop: Shop) -> dict:
    """Status shown on the settings page."""
    path = cookies_path(shop)
    status = {
        "imported": False,
        "count": 0,
        "imported_at": "",
        "expired_count": 0,
        "last_success": "",
    }
    if not path.exists():
        return status
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return status
    now = time.time()
    status["imported"] = True
    status["count"] = len(data)
    status["imported_at"] = datetime.fromtimestamp(path.stat().st_mtime).strftime(
        "%Y-%m-%d %H:%M"
    )
    for c in data:
        expires = c.get("expirationDate") or c.get("expires")
        try:
            if expires and float(expires) < now:
                status["expired_count"] += 1
        except (TypeError, ValueError):
            pass
    meta = _meta_path(shop)
    if meta.exists():
        try:
            ts = json.loads(meta.read_text(encoding="utf-8")).get("last_success")
            if ts:
                status["last_success"] = datetime.fromtimestamp(ts).strftime(
                    "%Y-%m-%d %H:%M"
                )
        except (json.JSONDecodeError, OSError):
            pass
    return status
