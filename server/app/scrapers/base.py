"""Scraper base class: Playwright fetch with imported session cookies.

Playwright is imported lazily inside ``_fetch_html`` so that parsing (and the
test suite) works without a browser installation.
"""
import re
from typing import ClassVar

from ..config import settings
from ..cookies import load_playwright_cookies
from ..models import Order, Shop

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ScrapeError(Exception):
    """Base class; the message is shown to the user."""


class SessionExpired(ScrapeError):
    """No/expired cookies — user must (re-)import them on the settings page."""

    def __init__(self, shop: Shop):
        self.shop = shop
        super().__init__(f"{shop.display_name} session expired")


class OrderNotFound(ScrapeError):
    pass


class ParseFailed(ScrapeError):
    """Page loaded but no items could be extracted (selectors outdated?)."""


# "1.234,56 €", "€12,34", "$12.34", "EUR 12,34" → (value, currency)
_PRICE_RE = re.compile(r"(?P<cur>[€$£]|EUR|USD|GBP)?\s*(?P<num>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{1,2})?)\s*(?P<cur2>[€$£]|EUR|USD|GBP)?")
_CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP", "EUR": "EUR", "USD": "USD", "GBP": "GBP"}


def parse_price(text: str) -> tuple[float | None, str]:
    """Best-effort price extraction from a text snippet."""
    match = _PRICE_RE.search(text or "")
    if not match:
        return None, "EUR"
    num = match.group("num")
    # Normalize decimal separator: the LAST separator is the decimal point.
    last_dot, last_comma = num.rfind("."), num.rfind(",")
    if last_comma > last_dot:
        num = num.replace(".", "").replace(",", ".")
    else:
        num = num.replace(",", "")
    currency = _CURRENCIES.get(match.group("cur") or match.group("cur2") or "", "EUR")
    try:
        return float(num), currency
    except ValueError:
        return None, currency


async def _abort_heavy_resources(route) -> None:
    if route.request.resource_type in ("image", "media", "font"):
        await route.abort()
    else:
        await route.continue_()


class Scraper:
    shop: ClassVar[Shop]
    ORDER_URL_TEMPLATE: ClassVar[str] = ""
    # Substrings of the final page URL that mean "redirected to login".
    LOGIN_URL_PATTERNS: ClassVar[tuple[str, ...]] = ()
    # Selector to wait for before grabbing the HTML (best effort).
    READY_SELECTOR: ClassVar[str] = "body"

    def order_url(self, order_no: str) -> str:
        return self.ORDER_URL_TEMPLATE.format(order_no=order_no)

    async def fetch_order(self, order_no: str) -> Order:
        html = await self._fetch_html(self.order_url(order_no))
        order = self.parse(html, order_no)
        if not order.items:
            raise ParseFailed(
                f"No items found on the {self.shop.display_name} order page"
            )
        return order

    def parse(self, html: str, order_no: str) -> Order:  # pragma: no cover
        raise NotImplementedError

    async def _fetch_html(self, url: str) -> str:
        cookies = load_playwright_cookies(self.shop)
        if not cookies:
            raise SessionExpired(self.shop)

        from playwright.async_api import (  # noqa: PLC0415 — lazy, see module docstring
            TimeoutError as PlaywrightTimeout,
            async_playwright,
        )

        timeout = settings.scraper_timeout_ms
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=settings.scraper_headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                context = await browser.new_context(
                    user_agent=USER_AGENT, locale="de-DE"
                )
                await context.add_cookies(cookies)
                page = await context.new_page()
                # Images/fonts/media aren't needed for parsing (src attributes
                # stay in the DOM) and shop pages load tracking assets forever.
                await page.route("**/*", _abort_heavy_resources)
                # "commit" returns as soon as the response starts — waiting for
                # domcontentloaded timed out on slow-loading Amazon pages even
                # though the content was long there. Readiness is handled by
                # the selector wait below; parse() reports unusable pages.
                try:
                    await page.goto(url, wait_until="commit", timeout=timeout)
                except PlaywrightTimeout:
                    pass  # a partial DOM may still be parseable
                try:
                    await page.wait_for_selector(self.READY_SELECTOR, timeout=timeout)
                except PlaywrightTimeout:
                    pass
                final_url = page.url
                if any(marker in final_url for marker in self.LOGIN_URL_PATTERNS):
                    raise SessionExpired(self.shop)
                return await page.content()
            finally:
                await browser.close()
