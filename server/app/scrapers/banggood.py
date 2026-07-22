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

        _rescale_to_goods_total(order.items, soup)
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


def _rescale_to_goods_total(items: list[OrderItemDraft], soup: BeautifulSoup) -> None:
    """Rescale row prices to the goods total actually paid (sub-total minus
    coupon/allowance/points discounts), so discounts end up in the purchase
    price. Shipping and the grand total are deliberately ignored — shipping is
    not part of an item's value (same intent as the Amazon/AliExpress/Temu
    rescale, adapted to Banggood's total breakdown)."""
    target = _goods_total(soup)
    if target is None:
        return
    priced = [item for item in items if item.unit_price]
    row_sum = sum(item.unit_price * item.quantity for item in priced)
    if not target or not row_sum or abs(row_sum - target) < 0.01:
        return
    factor = target / row_sum
    if not 0.5 <= factor <= 1.5:  # implausible → keep the row prices
        return
    for item in priced:
        item.unit_price = round(item.unit_price * factor, 2)


def _goods_total(soup: BeautifulSoup) -> float | None:
    """Sub-total plus any negative adjustment lines (discounts/coupons/points).

    The ``.order-detail-total`` block lists Sub-Total, discounts (negative),
    shipping (positive) and the grand Total. We start at Sub-Total and add the
    negative lines only — shipping and the derived grand Total are skipped.
    """
    sub_total: float | None = None
    adjustments = 0.0
    for row in soup.select(".order-detail-total .total-list-item"):
        name_el = row.select_one(".name")
        price_el = row.select_one(".price")
        if name_el is None or price_el is None:
            continue
        label = name_el.get_text(" ", strip=True).lower()
        raw = price_el.get_text(" ", strip=True)
        value, _ = parse_price(raw)
        if value is None:
            continue
        if "sub-total" in label or "subtotal" in label:
            sub_total = value
        elif "total" in label:
            continue  # grand total — derived from the lines above
        elif raw.lstrip().startswith("-"):
            adjustments -= value  # discount / coupon / points
        # positive non-subtotal lines (shipping) are not part of item value
    if sub_total is None:
        return None
    return round(sub_total + adjustments, 2)


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
