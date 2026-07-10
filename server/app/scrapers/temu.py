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
# misread (1993 → 1993.00 €). goodsRetailPrice* is the strike-through price —
# never list it here. Verified against a real dump: goodsPriceWithSymbolDisplay
# = "19,93€", goodsPriceDisplay = "19.93".
JSON_PRICE_KEYS = (
    "goodsPriceWithSymbolDisplay", "goodsPriceDisplay",
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
# Display price next to the item name ("19,93€" / "€19.93") — rawData often
# carries prices only as cent integers, so the rendered row is the source.
DOM_PRICE_RE = re.compile(r"€\s*\d{1,4}(?:[.,]\d{2})|\d{1,4}(?:[.,]\d{2})\s*€")
# Paid order total from the payment details block
TOTAL_RE = re.compile(r"(?:Gesamtsumme|Order total|Total)\s*:?\s*€?\s*(\d{1,5}(?:[.,]\d{2}))\s*€?")
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
        soup = BeautifulSoup(html, "html.parser")
        order.items = _parse_from_json(html) or _parse_from_dom(soup)
        if not order.items:
            raise ParseFailed("Temu: no order items found (page structure changed?)")
        _fill_prices_from_dom(order.items, soup)
        page_text = soup.get_text(" ", strip=True)
        _rescale_to_order_total(order.items, page_text)
        order.order_date = _parse_order_date(html, page_text)
        return order


def _parse_order_date(page_html: str, page_text: str) -> str | None:
    match = ORDER_TIME_JSON_RE.search(page_html)
    if match:
        epoch = int(match.group(1))
        if epoch >= 10**12:  # milliseconds
            epoch //= 1000
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")
    for date_re in ORDER_DATE_TEXT_RES:
        date_match = date_re.search(page_text)
        if date_match:
            month = MONTHS_ABBR.get(date_match["m"].lower()[:3])
            if month:
                return f"{date_match['y']}-{month:02d}-{int(date_match['d']):02d}"
    return None


def _fill_prices_from_dom(items: list[OrderItemDraft], soup: BeautifulSoup) -> None:
    """For items whose rawData blob had no price string, take the display
    price rendered next to the item name (climbing a few parents until the
    row container includes one)."""
    for item in items:
        if item.unit_price is not None or not item.name:
            continue
        prefix = item.name[:24]
        for node in soup.find_all(string=lambda s, p=prefix: s and p in s):
            if node.find_parent(["script", "style"]):
                continue
            el = node.parent
            for _ in range(5):
                if el is None or el.name in ("body", "html"):
                    break
                match = DOM_PRICE_RE.search(el.get_text(" ", strip=True))
                if match:
                    item.unit_price, item.currency = parse_price(match.group(0))
                    break
                el = el.parent
            break  # only the first non-script occurrence of the name


def _rescale_to_order_total(items: list[OrderItemDraft], page_text: str) -> None:
    """Rescale row prices to the paid "Gesamtsumme" so discounts end up in the
    purchase price (same idea as the Amazon/AliExpress rescale)."""
    match = TOTAL_RE.search(page_text)
    if not match:
        return
    total, _ = parse_price(match.group(1))
    priced = [item for item in items if item.unit_price]
    row_sum = sum(item.unit_price * item.quantity for item in priced)
    if not total or not row_sum or abs(row_sum - total) < 0.01:
        return
    factor = total / row_sum
    if not 0.5 <= factor <= 1.5:  # implausible → keep the row prices
        return
    for item in priced:
        item.unit_price = round(item.unit_price * factor, 2)


def _parse_from_json(page_html: str) -> list[OrderItemDraft]:
    """Extract flat goods objects out of the embedded rawData JSON.

    Temu spreads one order line over several blobs (one carries the price,
    another the thumbnail), so blobs with the same name are merged."""
    items: dict[str, OrderItemDraft] = {}
    for blob in _GOODS_BLOB_RE.findall(page_html):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        name = _first_key(data, JSON_NAME_KEYS)
        if not name:
            continue
        name = html_module.unescape(str(name))
        item = items.setdefault(name, OrderItemDraft(name=name))
        qty = _first_key(data, JSON_QTY_KEYS)
        if isinstance(qty, str) and qty.isdigit():
            qty = int(qty)
        if isinstance(qty, (int, float)) and qty >= 1:
            item.quantity = int(qty)
        if item.unit_price is None:
            price = _first_key(data, JSON_PRICE_KEYS)
            if isinstance(price, str) and price.strip():
                item.unit_price, item.currency = parse_price(price)
        if not item.image_url:
            thumb = _first_key(data, JSON_THUMB_KEYS)
            if thumb:
                item.image_url = str(thumb)
        if not item.product_url:
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
    return list(items.values())


def _parse_from_dom(soup: BeautifulSoup) -> list[OrderItemDraft]:
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
