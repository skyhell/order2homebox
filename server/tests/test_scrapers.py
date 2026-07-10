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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bestellung aufgegeben 7. Juli 2026", "2026-07-07"),  # real order-details page
        ("Bestellt am 3. Juli 2026", "2026-07-03"),
        ("Ordered on July 3, 2026", "2026-07-03"),
        ("Order placed July 12, 2026", "2026-07-12"),
        ("kein Datum hier", ""),
    ],
)
def test_amazon_order_date_variants(text, expected):
    from app.scrapers.amazon import _find_order_date

    assert _find_order_date(text) == expected


def test_amazon_price_skips_order_total_and_strike_price():
    """Regression: the order total (Gesamtsumme) and struck-through list
    prices must never land in the unit-price field."""
    html = """
    <html><body><div id="orderDetails">
      <div class="yohtmlc-item">
        <div id="od-subtotals">
          <span class="a-color-price">18,14 €</span><!-- Gesamtsumme -->
        </div>
        <a class="a-link-normal" href="/dp/B0CNN846VX">MZHOU Spannungswandler</a>
        <span class="a-price a-text-strike"><span class="a-offscreen">24,99 €</span></span>
        <span class="a-color-price">17,99 €</span>
      </div>
    </div></body></html>
    """
    order = AmazonScraper().parse(html, "028-9097847-9224331")
    assert len(order.items) == 1
    assert order.items[0].unit_price == 17.99


def test_amazon_price_rescaled_to_subtotal_for_foreign_vat():
    """Regression: amazon.de shows rows with German VAT (17,99 €) but Austrian
    customers pay 20% VAT — the subtotal (18,14 €) is what was charged."""
    html = """
    <html><body><div id="orderDetails">
      <span>Bestellung aufgegeben 7. Juli 2026</span>
      <div class="yohtmlc-item">
        <a class="a-link-normal" href="/dp/B0CNN846VX">MZHOU Spannungswandler</a>
        <span class="a-color-price">17,99 €</span>
      </div>
      <div id="od-subtotals">
        <span>Zwischensumme: </span><span class="a-color-price">18,14 €</span>
        <span>Verpackung &amp; Versand: </span><span class="a-color-price">0,00 €</span>
        <span>Gesamtsumme: </span><span class="a-color-price">18,14 €</span>
      </div>
    </div></body></html>
    """
    order = AmazonScraper().parse(html, "028-9097847-9224331")
    assert order.order_date == "2026-07-07"
    assert len(order.items) == 1
    assert order.items[0].unit_price == 18.14


def test_amazon_multi_item_order_rescales_to_summe_not_net_subtotal():
    """Regression (multi-item AT order): with a separate VAT row,
    'Zwischensumme' is the NET amount — 'Summe' (gross before vouchers) is
    the right rescale target. Rows 16,14+13,19 (DE VAT) → Summe 29,57 (AT VAT)."""
    html = """
    <html><body><div id="orderDetails">
      <span>Bestellung aufgegeben 7. Juli 2026</span>
      <div>Zwischensumme: 24,64 € Verpackung &amp; Versand: 0,00 €
           Gesamt vor USt.: 24,64 € Geschätzte USt.: 4,93 € Summe: 29,57 €
           Gutschein eingelöst: -2,96 € Gesamtsumme: 26,61 €</div>
      <div class="yohtmlc-item">
        <a class="a-link-normal" href="/dp/B0AAA11111">BYANE 3er-Pack Sägekette 40 cm</a>
        <span class="a-color-price">16,14 €</span>
      </div>
      <div class="yohtmlc-item">
        <a class="a-link-normal" href="/dp/B0BBB22222">QUARKZMAN 2 Stück 80mm Lüfter</a>
        <span class="a-color-price">13,19 €</span>
      </div>
    </div></body></html>
    """
    order = AmazonScraper().parse(html, "028-1674448-8402738")
    assert len(order.items) == 2
    saw_chain, fans = order.items
    # 29,57 / 29,33 rescale: exact per-item AT-VAT prices
    assert saw_chain.unit_price == 16.27
    assert fans.unit_price == 13.30
    assert round(saw_chain.unit_price + fans.unit_price, 2) == 29.57


def test_amazon_net_subtotal_not_used_when_vat_row_present():
    """Without a 'Summe' row but WITH a VAT row, no rescaling may happen —
    Zwischensumme would be the net amount and shrink the prices."""
    html = """
    <html><body><div id="orderDetails">
      <div>Zwischensumme: 24,64 € Geschätzte USt.: 4,93 €</div>
      <div class="yohtmlc-item">
        <a class="a-link-normal" href="/dp/B0AAA11111">Sägekette</a>
        <span class="a-color-price">16,14 €</span>
      </div>
      <div class="yohtmlc-item">
        <a class="a-link-normal" href="/dp/B0BBB22222">Lüfter</a>
        <span class="a-color-price">13,19 €</span>
      </div>
    </div></body></html>
    """
    order = AmazonScraper().parse(html, "028-1")
    assert [i.unit_price for i in order.items] == [16.14, 13.19]


# -- AliExpress ------------------------------------------------------------------


def test_aliexpress_parse():
    """German page, prices split into per-character spans, Gesamt == row sum."""
    order = AliExpressScraper().parse(
        load_fixture("aliexpress_order.html"), "8123456789012345"
    )
    assert order.order_date == "2026-07-03"  # "Bestellung aufgegeben am: 3. Jul 2026"
    assert len(order.items) == 2
    esp, jst = order.items
    assert esp.name == "ESP32 Development Board WiFi Bluetooth"
    assert esp.unit_price == 4.56  # <span>4</span><span>.</span><span>5</span>…
    assert esp.quantity == 2
    assert esp.description == "Color: Type-C CH340"
    assert esp.product_url.startswith("https://www.aliexpress.com/item/")
    assert esp.image_url.startswith("https://")
    assert jst.quantity == 1
    assert jst.unit_price == 2.99  # Gesamt matches the rows → no rescale


def test_aliexpress_english_order_date():
    html = """
    <html><body>
    <div class="order-info">Order date: Jul 3, 2026</div>
    <div class="order-detail-item-content-wrap">
      <div class="item-title"><a href="//www.aliexpress.com/item/1.html">Widget</a></div>
      <div class="item-price">€ 2.99 x1</div>
    </div>
    </body></html>
    """
    order = AliExpressScraper().parse(html, "81234")
    assert order.order_date == "2026-07-03"


def test_aliexpress_rescales_to_paid_total():
    """Coins/coupons reduce the paid total below the row prices — the item
    price must be what the user actually paid (regression: real order showed
    €33.85 x1, Gesamt €31.51)."""
    html = """
    <html><body>
    <div class="order-info">Bestellung aufgegeben am: 7. Jul 2026</div>
    <div class="order-detail-item-content-wrap">
      <div class="item-title"><a href="//www.aliexpress.com/item/1005012652240868.html">Podofo Kühlsystem-Lecktester-Set</a></div>
      <div class="item-sku-attr">Germany</div>
      <div class="item-price"><span>€</span><span>3</span><span>3</span><span>.</span><span>8</span><span>5</span> <span>x1</span></div>
    </div>
    <div class="order-summary">Zwischensumme €33.85 Gesamt <span>€</span><span>3</span><span>1</span><span>.</span><span>5</span><span>1</span></div>
    </body></html>
    """
    order = AliExpressScraper().parse(html, "3074598413332037")
    assert order.order_date == "2026-07-07"
    (item,) = order.items
    assert item.unit_price == 31.51
    assert item.quantity == 1
    assert item.description == "Germany"  # ships-from is the page's SKU line


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
