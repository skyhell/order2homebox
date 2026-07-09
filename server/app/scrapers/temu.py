"""Temu order-detail scraper.

Temu renders its order page from an embedded JSON blob (``window.rawData``).
We extract item data from that JSON with regexes (robust against class-name
churn) and fall back to DOM selectors. When Temu changes its page, this file
is the only place to touch.
"""
import html as html_module
import json
import re

from bs4 import BeautifulSoup

from ..models import Order, OrderItemDraft, Shop
from .base import ParseFailed, Scraper, parse_price

# ---------------------------------------------------------------- selectors
ORDER_URL_TEMPLATE = "https://www.temu.com/bgt_order_detail.html?parent_order_sn={order_no}"
# JSON keys inside window.rawData that describe one order line
JSON_NAME_KEYS = ("goods_name", "goodsName")
JSON_QTY_KEYS = ("goods_number", "goodsNumber", "quantity")
JSON_PRICE_KEYS = ("goods_price_str", "goodsPriceStr", "price_str")
JSON_THUMB_KEYS = ("thumb_url", "thumbUrl")
# DOM fallback (used when the JSON structure changed)
DOM_ITEM_SELECTORS = ["[class*='goodsWrapper']", "[class*='goods-item']"]
DOM_TITLE_SELECTORS = ["[class*='goodsName']", "[class*='goods-name']"]
# ---------------------------------------------------------------------------

_GOODS_BLOB_RE = re.compile(
    r'\{[^{}]*"(?:goods_name|goodsName)"\s*:\s*"(?:[^"\\]|\\.)*"[^{}]*\}'
)


class TemuScraper(Scraper):
    shop = Shop.temu
    ORDER_URL_TEMPLATE = ORDER_URL_TEMPLATE
    LOGIN_URL_PATTERNS = ("/login.html", "login_scene")
    READY_SELECTOR = "body"

    def parse(self, html: str, order_no: str) -> Order:
        order = Order(shop=self.shop, order_no=order_no)
        order.items = _parse_from_json(html) or _parse_from_dom(html)
        if not order.items:
            raise ParseFailed("Temu: no order items found (page structure changed?)")
        return order


def _parse_from_json(page_html: str) -> list[OrderItemDraft]:
    """Extract flat goods objects out of the embedded rawData JSON."""
    items: list[OrderItemDraft] = []
    seen: set[str] = set()
    for blob in _GOODS_BLOB_RE.findall(page_html):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        name = _first_key(data, JSON_NAME_KEYS)
        if not name or name in seen:
            continue
        seen.add(name)
        item = OrderItemDraft(name=html_module.unescape(str(name)))
        qty = _first_key(data, JSON_QTY_KEYS)
        if isinstance(qty, (int, float)) and qty >= 1:
            item.quantity = int(qty)
        price = _first_key(data, JSON_PRICE_KEYS)
        if price:
            item.unit_price, item.currency = parse_price(str(price))
        thumb = _first_key(data, JSON_THUMB_KEYS)
        if thumb:
            item.image_url = str(thumb)
        items.append(item)
    return items


def _parse_from_dom(page_html: str) -> list[OrderItemDraft]:
    soup = BeautifulSoup(page_html, "html.parser")
    nodes = []
    for selector in DOM_ITEM_SELECTORS:
        nodes = soup.select(selector)
        if nodes:
            break
    items = []
    for node in nodes:
        title_el = None
        for selector in DOM_TITLE_SELECTORS:
            title_el = node.select_one(selector)
            if title_el is not None:
                break
        if title_el is None:
            continue
        item = OrderItemDraft(name=title_el.get_text(" ", strip=True))
        item.unit_price, item.currency = parse_price(node.get_text(" ", strip=True))
        img = node.select_one("img")
        if img is not None:
            item.image_url = img.get("src", "")
        if item.name:
            items.append(item)
    return items


def _first_key(data: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in data:
            return data[key]
    return None
