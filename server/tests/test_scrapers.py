from pathlib import Path

import pytest

from app.models import Shop
from app.scrapers import ParseFailed, get_scraper
from app.scrapers.aliexpress import AliExpressScraper
from app.scrapers.amazon import AmazonScraper
from app.scrapers.base import parse_price
from app.scrapers.temu import TemuScraper

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# -- price parsing ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "currency"),
    [
        ("24,99 €", 24.99, "EUR"),
        ("€ 4,56", 4.56, "EUR"),
        ("1.234,56 €", 1234.56, "EUR"),
        ("$12.34", 12.34, "USD"),
        ("1,234.56 USD", 1234.56, "USD"),
        ("EUR 5", 5.0, "EUR"),
        ("no price here", None, "EUR"),
    ],
)
def test_parse_price(text, value, currency):
    assert parse_price(text) == (value, currency)


# -- registry -----------------------------------------------------------------


def test_registry_returns_correct_scraper():
    assert isinstance(get_scraper(Shop.amazon), AmazonScraper)
    assert isinstance(get_scraper(Shop.aliexpress), AliExpressScraper)
    assert isinstance(get_scraper(Shop.temu), TemuScraper)


# -- Amazon --------------------------------------------------------------------


def test_amazon_parse():
    order = AmazonScraper().parse(load_fixture("amazon_order.html"), "302-1234567-1234567")
    assert order.order_no == "302-1234567-1234567"
    assert order.order_date == "2026-07-03"
    assert len(order.items) == 2
    hub, sd = order.items
    assert hub.name == "USB-C Hub 7-in-1 Aluminium"
    assert hub.unit_price == 24.99
    assert hub.currency == "EUR"
    assert hub.quantity == 2
    assert hub.product_url == "https://www.amazon.de/dp/B0TEST123"
    assert hub.image_url.endswith("test1.jpg")
    assert sd.quantity == 1


def test_amazon_parse_failed_on_empty_page():
    with pytest.raises(ParseFailed):
        AmazonScraper().parse("<html><body>nix</body></html>", "123")


# -- AliExpress ------------------------------------------------------------------


def test_aliexpress_parse():
    order = AliExpressScraper().parse(
        load_fixture("aliexpress_order.html"), "8123456789012345"
    )
    assert order.order_date == "2026-07-03"
    assert len(order.items) == 2
    esp, jst = order.items
    assert esp.name == "ESP32 Development Board WiFi Bluetooth"
    assert esp.unit_price == 4.56
    assert esp.quantity == 2
    assert esp.description == "Color: Type-C CH340"
    assert esp.product_url.startswith("https://www.aliexpress.com/item/")
    assert esp.image_url.startswith("https://")
    assert jst.quantity == 1


# -- Temu -------------------------------------------------------------------------


def test_temu_parse_from_embedded_json():
    order = TemuScraper().parse(load_fixture("temu_order.html"), "PO-211-12345678901234567")
    assert len(order.items) == 2
    screwdriver, ties = order.items
    assert screwdriver.name == "Mini Schraubendreher Set 25 in 1"
    assert screwdriver.quantity == 1
    assert screwdriver.unit_price == 3.48
    assert screwdriver.image_url.startswith("https://img.kwcdn.com/")
    assert ties.name == "Kabelbinder wiederverwendbar 100 Stück"
    assert ties.quantity == 2


def test_temu_parse_failed_on_empty_page():
    with pytest.raises(ParseFailed):
        TemuScraper().parse("<html><body></body></html>", "PO-1")
