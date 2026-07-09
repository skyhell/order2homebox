"""Amazon order-details scraper.

All selectors live in the constants below — when Amazon changes its page,
this file is the only place to touch. Selector lists are tried in order;
the first one that matches wins.
"""
import re

from bs4 import BeautifulSoup

from ..config import settings
from ..models import Order, OrderItemDraft, Shop
from .base import ParseFailed, Scraper, parse_price

# ---------------------------------------------------------------- selectors
ITEM_SELECTORS = [
    ".yohtmlc-item",                                # order-details (2024+)
    "[data-component='purchasedItems'] .a-fixed-left-grid",
    ".a-box.shipment .a-fixed-left-grid",           # older shipment boxes
]
TITLE_SELECTORS = [
    ".yohtmlc-product-title",
    "[data-component='itemTitle'] a",
    "a.a-link-normal[href*='/dp/']",
    "a.a-link-normal[href*='/gp/product/']",
]
PRICE_SELECTORS = [
    ".yohtmlc-item .a-color-price",
    "[data-component='unitPrice'] .a-offscreen",
    ".a-color-price",
    "span.a-price .a-offscreen",
]
QTY_SELECTORS = [
    ".od-item-view-qty",
    "[data-component='itemQuantity']",
    "span.item-view-qty",
]
# "Bestellt am 3. Juli 2026" / "Ordered on July 3, 2026"
ORDER_DATE_RE = re.compile(
    r"(?:Bestellt am|Ordered on)\s+(\d{1,2})\.?\s*([A-Za-zäöü]+)\s+(\d{4})"
)
MONTHS = {
    "januar": 1, "january": 1, "februar": 2, "february": 2, "märz": 3, "march": 3,
    "april": 4, "mai": 5, "may": 5, "juni": 6, "june": 6, "juli": 7, "july": 7,
    "august": 8, "september": 9, "oktober": 10, "october": 10,
    "november": 11, "dezember": 12, "december": 12,
}
# ---------------------------------------------------------------------------


class AmazonScraper(Scraper):
    shop = Shop.amazon
    LOGIN_URL_PATTERNS = ("/ap/signin", "/ap/cvf/")
    READY_SELECTOR = "#orderDetails, .yohtmlc-item, .a-box.shipment"

    def order_url(self, order_no: str) -> str:
        # Domain is configurable (O2H_AMAZON_DOMAIN, default www.amazon.de)
        return (
            f"https://{settings.amazon_domain}"
            f"/gp/your-account/order-details?orderID={order_no}"
        )

    def parse(self, html: str, order_no: str) -> Order:
        soup = BeautifulSoup(html, "html.parser")
        order = Order(shop=self.shop, order_no=order_no)
        order.order_date = _find_order_date(soup.get_text(" ", strip=True))

        nodes = _first_match_all(soup, ITEM_SELECTORS)
        if not nodes:
            raise ParseFailed("Amazon: no order items found (selectors outdated?)")
        for node in nodes:
            item = OrderItemDraft()
            title_el = _first_match(node, TITLE_SELECTORS)
            if title_el is None:
                continue
            item.name = title_el.get_text(" ", strip=True)
            href = title_el.get("href", "")
            if href.startswith("/"):
                href = f"https://{settings.amazon_domain}{href}"
            item.product_url = href.split("?")[0]

            price_el = _first_match(node, PRICE_SELECTORS)
            if price_el is not None:
                item.unit_price, item.currency = parse_price(price_el.get_text())

            qty_el = _first_match(node, QTY_SELECTORS)
            if qty_el is not None:
                digits = re.search(r"\d+", qty_el.get_text())
                if digits:
                    item.quantity = int(digits.group())

            img = node.select_one("img")
            if img is not None:
                item.image_url = img.get("src", "")

            if item.name:
                order.items.append(item)
        return order


def _first_match(node, selectors: list[str]):
    for selector in selectors:
        found = node.select_one(selector)
        if found is not None:
            return found
    return None


def _first_match_all(soup, selectors: list[str]) -> list:
    for selector in selectors:
        found = soup.select(selector)
        if found:
            return found
    return []


def _find_order_date(text: str) -> str:
    match = ORDER_DATE_RE.search(text)
    if not match:
        return ""
    day, month_name, year = match.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return ""
    return f"{year}-{month:02d}-{int(day):02d}"
