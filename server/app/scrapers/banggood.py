"""Banggood order-detail scraper.

Banggood renders the order detail as a classic server-side page (a product
table inside the account area), so plain DOM selectors work — no embedded JSON
like Temu. All selectors live in the constants below; when Banggood changes its
page, this file is the only place to touch. Selector lists are tried in order,
the first one that matches wins.

Selectors were verified against a real "Orders Detail" page (2026).
"""
import re

from bs4 import BeautifulSoup

from ..models import Order, OrderItemDraft, Shop
from .base import OrderNotFound, ParseFailed, Scraper, parse_price

# ---------------------------------------------------------------- selectors
# order_no is the numeric Banggood order id (e.g. "116598360"), shown as the
# ordersId in the account order list/detail URLs. Note the plural spellings
# (t=ordersDetail, ordersId) and the required version=2 — verified against a
# real account page (the singular t=orderDetail&order_id form just redirects to
# the orders list).
ORDER_URL_TEMPLATE = (
    "https://www.banggood.com/index.php"
    "?com=account&t=ordersDetail&ordersId={order_no}&version=2&status=0"
)
# One row per ordered product. The header row lives in .product-list-hd, so the
# item rows must be scoped to the body block .product-list-bd.
ITEM_SELECTORS = [
    ".product-list-bd .list-row",
    ".order-detail-product .product-list-bd .list-row",
]
TITLE_SELECTORS = [
    ".list-item-detail .title a",
    ".title a",
    "a[href*='-p-']",
]
# Item unit price ("86,26€"); the .list-item-amount cell is the line total
# (unit × qty) and serves as a fallback.
PRICE_SELECTORS = [
    ".price-info .price",
    ".list-item-amount .text-wrap",
]
STRIKE_MARKERS = ("del", "old_price", "market_price", "line-through", "strike")
QTY_SELECTORS = [
    ".list-item-status .text-wrap",
    ".list-item-status",
]
# Variant / SKU shown under the title ("Typ: Mit EU-Smart-Steckdose").
ATTR_SELECTORS = [".poa-list", ".attr-item"]
# Order timestamp: "Order Time: 2026-05-20 13:45:19" (already ISO) —
# German pages render "Bestellzeit:" with the same yyyy-mm-dd date.
ORDER_DATE_RE = re.compile(
    r"(?:Order Time|Order Date|Bestellzeit|Bestelldatum)\s*:?\s*(\d{4})-(\d{2})-(\d{2})"
)
# Banggood redirects an unknown/foreign order id (and an expired session that
# still has *some* cookies) back to the account order LIST instead of a detail
# page. These markers identify that list page so we can report a clear error.
ORDER_LIST_MARKERS = ("account-index-transport", "com=account&amp;t=ordersList")
# ---------------------------------------------------------------------------


class BanggoodScraper(Scraper):
    shop = Shop.banggood
    ORDER_URL_TEMPLATE = ORDER_URL_TEMPLATE
    LOGIN_URL_PATTERNS = ("com=account&t=login", "/login", "account-login")
    READY_SELECTOR = ".order-detail-product, .product-list-bd, .account-index-transport"

    def parse(self, html: str, order_no: str) -> Order:
        soup = BeautifulSoup(html, "html.parser")
        order = Order(shop=self.shop, order_no=order_no)

        page_text = soup.get_text(" ", strip=True)
        order.order_date = _find_order_date(page_text)

        nodes = _first_match_all(soup, ITEM_SELECTORS)
        if not nodes:
            if any(marker in html for marker in ORDER_LIST_MARKERS):
                raise OrderNotFound(
                    "Banggood returned the order list, not a single order — "
                    "check the order number, or refresh the cookies in settings."
                )
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

            variant = _first_match(node, ATTR_SELECTORS)
            if variant is not None:
                item.description = variant.get_text(" ", strip=True)

            if item.name:
                order.items.append(item)

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
        match = re.search(r"\d+", qty_el.get_text(" ", strip=True))
        if match:
            return int(match.group())
    return 1


def _find_order_date(text: str) -> str:
    match = ORDER_DATE_RE.search(text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return ""


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
