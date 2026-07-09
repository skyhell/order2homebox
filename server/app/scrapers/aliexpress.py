"""AliExpress order-detail scraper.

All selectors live in the constants below — when AliExpress changes its page,
this file is the only place to touch.
"""
import re

from bs4 import BeautifulSoup

from ..models import Order, OrderItemDraft, Shop
from .base import ParseFailed, Scraper, parse_price

# ---------------------------------------------------------------- selectors
ORDER_URL_TEMPLATE = "https://www.aliexpress.com/p/order/detail.html?orderId={order_no}"
ITEM_SELECTORS = [
    ".order-detail-item-content-wrap",
    "[class*='order-detail-item-content']",
]
TITLE_SELECTORS = [
    ".item-title a",
    ".item-title",
    "a[href*='/item/']",
]
PRICE_SELECTORS = [
    ".item-price",
    "[class*='item-price']",
]
QTY_RE = re.compile(r"[x×]\s*(\d+)")
# "Order date: Jul 3, 2026" (also present in the order-info block)
ORDER_DATE_RE = re.compile(
    r"(?:Order date|Bestelldatum)[:\s]+([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{4})"
)
MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
# ---------------------------------------------------------------------------


class AliExpressScraper(Scraper):
    shop = Shop.aliexpress
    ORDER_URL_TEMPLATE = ORDER_URL_TEMPLATE
    LOGIN_URL_PATTERNS = ("login.aliexpress", "/login", "passport.aliexpress")
    READY_SELECTOR = "[class*='order-detail-item-content'], .order-detail-item-content-wrap"

    def parse(self, html: str, order_no: str) -> Order:
        soup = BeautifulSoup(html, "html.parser")
        order = Order(shop=self.shop, order_no=order_no)

        date_match = ORDER_DATE_RE.search(soup.get_text(" ", strip=True))
        if date_match:
            mon, day, year = date_match.groups()
            month = MONTHS_ABBR.get(mon.lower()[:3])
            if month:
                order.order_date = f"{year}-{month:02d}-{int(day):02d}"

        nodes = []
        for selector in ITEM_SELECTORS:
            nodes = soup.select(selector)
            if nodes:
                break
        if not nodes:
            raise ParseFailed("AliExpress: no order items found (selectors outdated?)")

        for node in nodes:
            item = OrderItemDraft()
            title_el = None
            for selector in TITLE_SELECTORS:
                title_el = node.select_one(selector)
                if title_el is not None:
                    break
            if title_el is None:
                continue
            item.name = title_el.get_text(" ", strip=True)
            href = title_el.get("href", "")
            if href.startswith("//"):
                href = f"https:{href}"
            item.product_url = href.split("?")[0]

            for selector in PRICE_SELECTORS:
                price_el = node.select_one(selector)
                if price_el is not None:
                    text = price_el.get_text(" ", strip=True)
                    item.unit_price, item.currency = parse_price(text)
                    qty = QTY_RE.search(text)
                    if qty:
                        item.quantity = int(qty.group(1))
                    break
            if item.quantity == 1:
                qty = QTY_RE.search(node.get_text(" ", strip=True))
                if qty:
                    item.quantity = int(qty.group(1))

            img = node.select_one("img")
            if img is not None:
                src = img.get("src", "")
                item.image_url = f"https:{src}" if src.startswith("//") else src

            # Variant info (color/size) is useful in the description
            variant = node.select_one("[class*='item-sku'], .item-sku-attr")
            if variant is not None:
                item.description = variant.get_text(" ", strip=True)

            if item.name:
                order.items.append(item)
        return order
