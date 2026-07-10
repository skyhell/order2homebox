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
# German page: "Bestellung aufgegeben am: 7. Jul 2026" (day first);
# English page: "Order date: Jul 3, 2026" / "Order placed on: Jul 3, 2026"
ORDER_DATE_RES = [
    re.compile(
        r"(?:Bestellung aufgegeben am|Bestelldatum|Bezahlt am)"
        r"[:\s]+(?P<d>\d{1,2})\.\s*(?P<m>[A-Za-zäÄ]{3,9})\.?\s+(?P<y>\d{4})"
    ),
    re.compile(
        r"(?:Order (?:date|placed(?: on)?)|Paid on)"
        r"[:\s]+(?P<m>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2}),\s*(?P<y>\d{4})"
    ),
]
MONTHS_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "mär": 3, "mrz": 3, "apr": 4, "may": 5,
    "mai": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "okt": 10,
    "nov": 11, "dec": 12, "dez": 12,
}
# Paid order total ("Gesamt €31.51") — after coins/coupons, so row prices get
# rescaled to it (same idea as the Amazon "Summe:" rescale). Matched against
# the compact page text (no separators, see parse()).
TOTAL_RE = re.compile(r"(?:Gesamt|Total)\s*:?\s*€?\s*(\d+(?:[.,]\d{1,2})?)")
# ---------------------------------------------------------------------------


class AliExpressScraper(Scraper):
    shop = Shop.aliexpress
    ORDER_URL_TEMPLATE = ORDER_URL_TEMPLATE
    LOGIN_URL_PATTERNS = ("login.aliexpress", "/login", "passport.aliexpress")
    READY_SELECTOR = "[class*='order-detail-item-content'], .order-detail-item-content-wrap"

    def parse(self, html: str, order_no: str) -> Order:
        soup = BeautifulSoup(html, "html.parser")
        order = Order(shop=self.shop, order_no=order_no)

        page_text = soup.get_text(" ", strip=True)
        for date_re in ORDER_DATE_RES:
            date_match = date_re.search(page_text)
            if date_match:
                month = MONTHS_ABBR.get(date_match["m"].lower()[:3])
                if month:
                    order.order_date = (
                        f"{date_match['y']}-{month:02d}-{int(date_match['d']):02d}"
                    )
                break

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
                    # AliExpress splits prices into per-character <span>s —
                    # join without separators so "€ 3 3 . 8 5" stays 33.85.
                    text = price_el.get_text("", strip=True)
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

        _rescale_to_order_total(order.items, soup.get_text("", strip=True))
        return order


def _rescale_to_order_total(items: list[OrderItemDraft], compact_text: str) -> None:
    """Rescale row prices to the paid "Gesamt" total (coins/coupon discounts).

    ``compact_text`` must be the page text joined WITHOUT separators because of
    the per-character price <span>s.
    """
    match = TOTAL_RE.search(compact_text)
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
