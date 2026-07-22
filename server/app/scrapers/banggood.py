"""Banggood order-detail scraper.

Banggood renders the order detail as a classic server-side page (a product
table inside the account area), so plain DOM selectors work — no embedded JSON
like Temu. All selectors live in the constants below; when Banggood changes its
page, this file is the only place to touch. Selector lists are tried in order,
the first one that matches wins.

The selectors here were written against Banggood's account order-detail markup
and should be re-verified against a real dump (data/debug/banggood-last-fetch.html)
if a shop update breaks them.
"""
import re

from bs4 import BeautifulSoup

from ..models import Order, OrderItemDraft, Shop
from .base import ParseFailed, Scraper, parse_price

# ---------------------------------------------------------------- selectors
# order_no is the numeric Banggood order id shown as "Order Number" / URL order_id.
ORDER_URL_TEMPLATE = (
    "https://www.banggood.com/index.php?com=account&t=orderDetail&order_id={order_no}"
)
# One row per ordered product.
ITEM_SELECTORS = [
    ".order_product_list .product_item",
    ".orderProductList tr.product",
    "[class*='order'] [class*='product_item']",
    ".pro_list li",
]
TITLE_SELECTORS = [
    ".product_name a",
    ".pro_name a",
    "a.title",
    "a[href*='-p-']",
]
# Item unit price ("€12.34" / "12,34 €"); struck-through list prices are skipped.
PRICE_SELECTORS = [
    ".product_price .now_price",
    ".product_price",
    ".pro_price",
    "[class*='price']",
]
STRIKE_MARKERS = ("del", "old_price", "market_price", "line-through", "strike")
# Quantity ("x2" / "Qty: 2" / a bare number in the qty cell).
QTY_SELECTORS = [
    ".product_num",
    ".pro_num",
    "[class*='quantity']",
    "[class*='_num']",
]
QTY_RE = re.compile(r"(?:x|×|Qty[:\s]*|Menge[:\s]*)\s*(\d+)", re.IGNORECASE)
# "Order Date: Jul 3, 2026" (English) / "Bestelldatum: 3. Juli 2026" (German).
ORDER_DATE_RES = [
    re.compile(
        r"(?:Order Date|Order Time|Date of Order)\s*:?\s*"
        r"(?P<m>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2}),\s*(?P<y>\d{4})"
    ),
    re.compile(
        r"(?:Bestelldatum|Bestelldatum|Bestellzeit|Bestellung aufgegeben am)\s*:?\s*"
        r"(?P<d>\d{1,2})\.\s*(?P<m>[A-Za-zäöü]{3,9})\.?\s+(?P<y>\d{4})"
    ),
]
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "mär": 3, "mrz": 3, "apr": 4, "may": 5,
    "mai": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "okt": 10,
    "nov": 11, "dec": 12, "dez": 12,
}
# Paid order total after coupons/points ("Grand Total: €31.51" /
# "Order Total: 31,51 €") — row prices are rescaled to it, like the other shops.
TOTAL_RE = re.compile(
    r"(?:Grand Total|Order Total|Total Paid|Gesamtbetrag|Gesamtsumme)\s*:?\s*"
    r"(?:€\s*)?(?P<num>\d{1,4}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{2})?)\s*€?"
)
# ---------------------------------------------------------------------------


class BanggoodScraper(Scraper):
    shop = Shop.banggood
    ORDER_URL_TEMPLATE = ORDER_URL_TEMPLATE
    LOGIN_URL_PATTERNS = ("com=account&t=login", "/login", "account-login")
    READY_SELECTOR = (
        ".order_product_list, .orderProductList, .pro_list, [class*='product_item']"
    )

    def parse(self, html: str, order_no: str) -> Order:
        soup = BeautifulSoup(html, "html.parser")
        order = Order(shop=self.shop, order_no=order_no)

        page_text = soup.get_text(" ", strip=True)
        order.order_date = _find_order_date(page_text)

        nodes = _first_match_all(soup, ITEM_SELECTORS)
        if not nodes:
            raise ParseFailed("Banggood: no order items found (selectors outdated?)")

        for node in nodes:
            title_el = _first_match(node, TITLE_SELECTORS)
            if title_el is None:
                continue
            item = OrderItemDraft(name=title_el.get_text(" ", strip=True))
            href = title_el.get("href", "")
            if href.startswith("//"):
                href = f"https:{href}"
            elif href.startswith("/"):
                href = f"https://www.banggood.com{href}"
            item.product_url = href.split("?")[0]

            item.unit_price, item.currency = _price_for(node)
            item.quantity = _qty_for(node)

            img = node.select_one("img")
            if img is not None:
                src = img.get("data-src") or img.get("src", "")
                item.image_url = f"https:{src}" if src.startswith("//") else src

            # SKU / variant (colour, plug type, …) is useful in the description.
            variant = node.select_one("[class*='sku'], [class*='attr'], .product_poa")
            if variant is not None:
                item.description = variant.get_text(" ", strip=True)

            if item.name:
                order.items.append(item)

        _rescale_to_order_total(order.items, page_text)
        return order


def _price_for(node) -> tuple[float | None, str]:
    for selector in PRICE_SELECTORS:
        for el in node.select(selector):
            if _is_strike(el):
                continue
            value, currency = parse_price(el.get_text(" ", strip=True))
            if value is not None:
                return value, currency
    return None, "EUR"


def _is_strike(el) -> bool:
    for ancestor in [el, *el.parents]:
        if ancestor.name is None:
            continue
        if ancestor.name == "del":
            return True
        classes = " ".join(ancestor.get("class") or []).lower()
        if any(marker in classes for marker in STRIKE_MARKERS):
            return True
    return False


def _qty_for(node) -> int:
    qty_el = _first_match(node, QTY_SELECTORS)
    if qty_el is not None:
        text = qty_el.get_text(" ", strip=True)
        match = QTY_RE.search(text) or re.search(r"\d+", text)
        if match:
            return int(match.group(match.lastindex or 0))
    match = QTY_RE.search(node.get_text(" ", strip=True))
    if match:
        return int(match.group(1))
    return 1


def _find_order_date(text: str) -> str:
    for date_re in ORDER_DATE_RES:
        match = date_re.search(text)
        if match:
            month = MONTHS.get(match["m"].lower()[:3])
            if month:
                return f"{match['y']}-{month:02d}-{int(match['d']):02d}"
    return ""


def _rescale_to_order_total(items: list[OrderItemDraft], page_text: str) -> None:
    """Rescale row prices to the paid total so coupon/points discounts end up in
    the purchase price (same idea as the Amazon/AliExpress/Temu rescale)."""
    match = TOTAL_RE.search(page_text)
    if not match:
        return
    total, _ = parse_price(match.group("num"))
    priced = [item for item in items if item.unit_price]
    row_sum = sum(item.unit_price * item.quantity for item in priced)
    if not total or not row_sum or abs(row_sum - total) < 0.01:
        return
    factor = total / row_sum
    if not 0.5 <= factor <= 1.5:  # implausible → keep the row prices
        return
    for item in priced:
        item.unit_price = round(item.unit_price * factor, 2)


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
