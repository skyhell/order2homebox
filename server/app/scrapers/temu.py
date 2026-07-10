"""Temu order-detail scraper.

Temu renders its order page from an embedded JSON blob (``window.rawData``).
We extract item data from that JSON with regexes (robust against class-name
churn) and fall back to DOM selectors. When Temu changes its page, this file
is the only place to touch.
"""
import html as html_module
import json
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..models import Order, OrderItemDraft, Shop
from .base import ParseFailed, Scraper, parse_price

# ---------------------------------------------------------------- selectors
ORDER_URL_TEMPLATE = "https://www.temu.com/bgt_order_detail.html?parent_order_sn={order_no}"
# JSON keys inside window.rawData that describe one order line
JSON_NAME_KEYS = ("goods_name", "goodsName")
JSON_QTY_KEYS = ("goods_number", "goodsNumber", "quantity")
# Only string-valued price keys: numeric keys hold cent amounts and would be
# misread (3348 → 3348.00 €).
JSON_PRICE_KEYS = (
    "goods_price_str", "goodsPriceStr", "price_str", "priceStr",
    "unit_price_str", "unitPriceStr", "display_amount", "displayAmount",
)
JSON_THUMB_KEYS = ("thumb_url", "thumbUrl")
JSON_LINK_KEYS = ("link_url", "linkUrl", "seo_link_url", "seoLinkUrl")
JSON_GOODS_ID_KEYS = ("goods_id", "goodsId")
GOODS_URL_TEMPLATE = "https://www.temu.com/goods.html?goods_id={goods_id}"
# Order timestamp inside rawData (epoch seconds or milliseconds)
ORDER_TIME_JSON_RE = re.compile(
    r'"(?:parent_order_time|parentOrderTime|order_time|orderTime)"\s*:\s*"?(\d{13}|\d{10})'
)
# Rendered fallback: "Bestellzeit: 9. Jul 2026" / "Order time: Jul 9, 2026"
ORDER_DATE_TEXT_RES = [
    re.compile(
        r"(?:Bestellzeit|Bestelldatum)[:\s]+(?P<d>\d{1,2})\.\s*(?P<m>[A-Za-zäÄ]{3,9})\.?\s+(?P<y>\d{4})"
    ),
    re.compile(
        r"(?:Order (?:time|date))[:\s]+(?P<m>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2}),\s*(?P<y>\d{4})"
    ),
]
MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "mär": 3, "mrz": 3, "apr": 4, "may": 5,
    "mai": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "okt": 10,
    "nov": 11, "dec": 12, "dez": 12,
}
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
        order.order_date = _parse_order_date(html)
        return order


def _parse_order_date(page_html: str) -> str | None:
    match = ORDER_TIME_JSON_RE.search(page_html)
    if match:
        epoch = int(match.group(1))
        if epoch >= 10**12:  # milliseconds
            epoch //= 1000
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")
    text = BeautifulSoup(page_html, "html.parser").get_text(" ", strip=True)
    for date_re in ORDER_DATE_TEXT_RES:
        date_match = date_re.search(text)
        if date_match:
            month = MONTHS_ABBR.get(date_match["m"].lower()[:3])
            if month:
                return f"{date_match['y']}-{month:02d}-{int(date_match['d']):02d}"
    return None


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
        if isinstance(qty, str) and qty.isdigit():
            qty = int(qty)
        if isinstance(qty, (int, float)) and qty >= 1:
            item.quantity = int(qty)
        price = _first_key(data, JSON_PRICE_KEYS)
        if isinstance(price, str) and price.strip():
            item.unit_price, item.currency = parse_price(price)
        thumb = _first_key(data, JSON_THUMB_KEYS)
        if thumb:
            item.image_url = str(thumb)
        link = _first_key(data, JSON_LINK_KEYS)
        goods_id = _first_key(data, JSON_GOODS_ID_KEYS)
        if link:
            link = str(link)
            if link.startswith("//"):
                link = f"https:{link}"
            elif link.startswith("/"):
                link = f"https://www.temu.com{link}"
            item.product_url = link
        elif goods_id:
            item.product_url = GOODS_URL_TEMPLATE.format(goods_id=goods_id)
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
