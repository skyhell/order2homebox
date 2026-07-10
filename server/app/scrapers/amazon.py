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
# Tried in order; matches inside order-summary blocks or struck-through
# list prices are skipped (see _price_for).
PRICE_SELECTORS = [
    "[data-component='unitPrice'] .a-offscreen",
    "[data-component='unitPrice'] .a-color-price",
    ".yohtmlc-item .a-color-price",
    "span.a-price .a-offscreen",
    ".a-color-price",
]
# Ancestors that mark a price as NOT the item price (order totals, shipping)
SUMMARY_ID_MARKERS = ("subtotals", "ordersummary")
SUMMARY_COMPONENT_MARKERS = ("orderSummary", "orderSubtotals", "shipmentTotal")
# "Zwischensumme: 18,14 €" — the subtotal reflects the VAT actually charged
# (e.g. Austrian 20% for AT customers), while the item rows show the price
# with German VAT. Row prices are rescaled to this subtotal (see parse()).
SUBTOTAL_RE = re.compile(
    r"(?:Zwischensumme|Artikelsumme|Item\(s\) Subtotal|Items Subtotal)\s*:?\s*"
    r"(EUR\s*[\d.,]+|[\d.,]+\s*€|€\s*[\d.,]+)"
)
QTY_SELECTORS = [
    ".od-item-view-qty",
    "[data-component='itemQuantity']",
    "span.item-view-qty",
]
# "Bestellung aufgegeben 7. Juli 2026" / "Bestellt am 3. Juli 2026" /
# "Ordered on July 3, 2026" / "Order placed July 3, 2026"
ORDER_DATE_RE = re.compile(
    r"(?:Bestellung aufgegeben|Bestellt am|Ordered on|Order placed)\s+"
    r"(?:(\d{1,2})\.?\s*)?([A-Za-zäöü]+)\s+(?:(\d{1,2}),\s*)?(\d{4})"
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
        page_text = soup.get_text(" ", strip=True)
        order = Order(shop=self.shop, order_no=order_no)
        order.order_date = _find_order_date(page_text)

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

            item.unit_price, item.currency = _price_for(node)

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

        _rescale_to_subtotal(order.items, page_text)
        return order


def _rescale_to_subtotal(items, page_text: str) -> None:
    """Rescale row prices to the order subtotal ("Zwischensumme").

    amazon.de shows item rows with German VAT, but customers in other EU
    countries (e.g. Austria, 20%) are charged their local VAT — only the
    subtotal reflects what was actually paid. Rescaling proportionally fixes
    single-item orders exactly and multi-item orders to a close approximation.
    """
    match = SUBTOTAL_RE.search(page_text)
    if not match or not items:
        return
    subtotal, _ = parse_price(match.group(1))
    if not subtotal:
        return
    if any(item.unit_price is None for item in items):
        return
    row_total = sum(item.unit_price * item.quantity for item in items)
    if row_total <= 0:
        return
    factor = subtotal / row_total
    if not 0.7 <= factor <= 1.3:  # sanity guard against mismatched numbers
        return
    for item in items:
        item.unit_price = round(item.unit_price * factor, 2)


def _price_for(node) -> tuple[float | None, str]:
    """Item unit price from a node — skipping order totals and struck-through
    list prices, which caused the order total to land in the price field."""
    for selector in PRICE_SELECTORS:
        for el in node.select(selector):
            if _is_summary_or_strike(el):
                continue
            value, currency = parse_price(el.get_text())
            if value is not None:
                return value, currency
    return None, "EUR"


def _is_summary_or_strike(el) -> bool:
    for ancestor in [el, *el.parents]:
        if ancestor.name is None:
            continue
        classes = " ".join(ancestor.get("class") or [])
        if "a-text-strike" in classes or ancestor.get("data-a-strike") == "true":
            return True
        el_id = (ancestor.get("id") or "").lower()
        if any(marker in el_id for marker in SUMMARY_ID_MARKERS):
            return True
        if (ancestor.get("data-component") or "") in SUMMARY_COMPONENT_MARKERS:
            return True
    return False


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
    day_de, month_name, day_en, year = match.groups()
    day = day_de or day_en
    month = MONTHS.get(month_name.lower())
    if not month or not day:
        return ""
    return f"{year}-{month:02d}-{int(day):02d}"
