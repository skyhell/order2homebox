from pathlib import Path

import pytest

from app.models import Shop
from app.scrapers import ParseFailed, get_scraper
from app.scrapers.aliexpress import AliExpressScraper
from app.scrapers.amazon import AmazonScraper
from app.scrapers.banggood import BanggoodScraper
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
    assert isinstance(get_scraper(Shop.banggood), BanggoodScraper)


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
    assert order.order_date == "2026-07-09"  # "parent_order_time" epoch in rawData
    assert len(order.items) == 2
    screwdriver, ties = order.items
    assert screwdriver.name == "Mini Schraubendreher Set 25 in 1"
    assert screwdriver.quantity == 1
    assert screwdriver.unit_price == 3.48
    assert screwdriver.image_url.startswith("https://img.kwcdn.com/")
    assert screwdriver.product_url == "https://www.temu.com/goods.html?goods_id=601099512345"
    assert ties.name == "Kabelbinder wiederverwendbar 100 Stück"
    assert ties.quantity == 2


def test_temu_order_date_from_rendered_text():
    """When rawData carries no usable timestamp, fall back to the rendered
    German "Bestellzeit:" line (day-first)."""
    html = """
    <html><body>
    <script>window.rawData = {"store":{"goods_list":[
    {"goods_name":"Mechanikerhocker","goods_number":2}
    ]}};</script>
    <div><span>Bestellzeit:</span> <span>9. Jul 2026</span></div>
    </body></html>
    """
    order = TemuScraper().parse(html, "PO-013-19245103158392955")
    assert order.order_date == "2026-07-09"
    assert order.items[0].quantity == 2


def test_temu_ignores_numeric_cent_prices():
    """Numeric price keys hold cent amounts — they must not be parsed as EUR."""
    html = """
    <html><body>
    <script>window.rawData = {"goods_list":[
    {"goods_name":"Hocker","goods_number":"2","goods_price":1993}
    ]}};</script>
    </body></html>
    """
    order = TemuScraper().parse(html, "PO-1")
    (item,) = order.items
    assert item.unit_price is None
    assert item.quantity == 2


def test_temu_merges_split_goods_blobs():
    """Structure taken from a real dump: one blob carries orderTime + the
    display price (goodsPriceWithSymbolDisplay), a second one the thumbnail.
    The strike-through goodsRetailPrice* must not win, and both blobs must be
    merged into one item."""
    html = """
    <html><body><script>window.rawData = {"a":[
    {"orderSn":"013-19245061215352955","orderTime":1783632103,"orderTimeFormat":"9. Jul. 2026, 23:21 Uhr","goodsId":604235240415538,"skuId":60844654244219,"goodsRetailPrice":3713,"goodsRetailPriceDisplay":"37.13","goodsRetailPriceWithSymbolDisplay":"37,13€","goodsAmount":3986,"goodsPrice":1993,"goodsPriceDisplay":"19.93","goodsPriceWithSymbolDisplay":"19,93€","symbol":"€","goodsNumber":2,"goodsName":"VEVOR Mechanikerhocker, 250 LBS Rollender Pneumatischer Werkstattstuhl"},
    {"goodsId":604235240415538,"goodsSkuId":60844654244219,"quantity":2,"thumbUrl":"https://img-eu.kwcdn.com/local-goods-img/test.jpg","goodsName":"VEVOR Mechanikerhocker, 250 LBS Rollender Pneumatischer Werkstattstuhl"}
    ]};</script></body></html>
    """
    order = TemuScraper().parse(html, "PO-013-19245103158392955")
    (item,) = order.items
    assert item.unit_price == 19.93
    assert item.quantity == 2
    assert item.image_url == "https://img-eu.kwcdn.com/local-goods-img/test.jpg"
    assert item.product_url == "https://www.temu.com/goods.html?goods_id=604235240415538"
    assert order.order_date == "2026-07-09"  # orderTime epoch


def test_temu_price_from_rendered_row():
    """rawData carries prices only as cent integers — the display price next
    to the item name must be used instead (regression: real order showed an
    empty price for '19,93€ ×2'). The coupon banner's '5,00€' must not win."""
    html = """
    <html><body>
    <div class="banner">Bestellgarantie | 5,00€ Gutschrift bei verspäteter Lieferung</div>
    <script>window.rawData = {"store":{"parent_order_time":1783598400,"goods_list":[
    {"goods_id":601099770001,"goods_name":"VEVOR Mechanikerhocker, 250 LBS Rollender Pneumatischer Werkstattstuhl, Höhenverstellbar","goods_number":2,"goods_price":1993}
    ]}};</script>
    <div class="order-goods-row">
      <div><span>VEVOR Mechanikerhocker, 250 LBS Rollender Pneumatischer Werkstattstuhl, Höhenverstel...</span></div>
      <div><span>19,93€</span><span>×2</span></div>
    </div>
    <div class="payment">Zahlungsdetails Gesamtsumme: 39,86€ Artikel gesamt: 54,18€ Artikel-Rabatt: -14,32€</div>
    </body></html>
    """
    order = TemuScraper().parse(html, "PO-013-19245103158392955")
    (item,) = order.items
    assert item.unit_price == 19.93
    assert item.quantity == 2
    assert order.order_date == "2026-07-09"
    assert item.product_url == "https://www.temu.com/goods.html?goods_id=601099770001"


def test_temu_rescales_to_paid_total():
    """When the row still shows the pre-discount price, rescale to the paid
    Gesamtsumme (27,09€ × 2 = 54,18€ → 39,86€ paid → 19,93€ each)."""
    html = """
    <html><body>
    <script>window.rawData = {"goods_list":[
    {"goods_name":"VEVOR Mechanikerhocker, 250 LBS Rollender Werkstattstuhl","goods_number":2}
    ]}};</script>
    <div class="order-goods-row">
      <span>VEVOR Mechanikerhocker, 250 LBS Rollender Werkstattstuhl</span>
      <span>27,09€</span>
    </div>
    <div class="payment">Gesamtsumme: 39,86€</div>
    </body></html>
    """
    order = TemuScraper().parse(html, "PO-1")
    assert order.items[0].unit_price == 19.93


def test_temu_parse_failed_on_empty_page():
    with pytest.raises(ParseFailed):
        TemuScraper().parse("<html><body></body></html>", "PO-1")


# -- Banggood ---------------------------------------------------------------------


def test_banggood_parse():
    order = BanggoodScraper().parse(load_fixture("banggood_order.html"), "108123456789")
    assert order.order_no == "108123456789"
    assert order.order_date == "2026-07-03"
    assert len(order.items) == 2

    meter, iron = order.items
    assert meter.name == "Digital Multimeter True RMS Auto Ranging"
    assert meter.quantity == 2
    assert meter.currency == "EUR"
    # Struck-through list price (€24.90) must be ignored in favour of now_price.
    assert meter.product_url == "https://www.banggood.com/Digital-Multimeter-True-RMS-p-1234567.html"
    assert meter.image_url == "https://img.banggood.com/thumb/large/test1.jpg"
    assert "Black" in meter.description

    # Protocol-relative product URL is normalised to https.
    assert iron.product_url == "https://www.banggood.com/Soldering-Iron-Kit-p-7654321.html"
    assert iron.quantity == 1


def test_banggood_rescales_to_grand_total():
    # Rows: 18.99*2 + 12.50 = 50.48; Grand Total 45.48 → factor 0.90095.
    order = BanggoodScraper().parse(load_fixture("banggood_order.html"), "108123456789")
    meter, iron = order.items
    assert meter.unit_price == 17.11
    assert iron.unit_price == 11.26


def test_banggood_parse_failed_on_empty_page():
    with pytest.raises(ParseFailed):
        BanggoodScraper().parse("<html><body></body></html>", "1")


def test_banggood_redirect_to_order_list_raises_not_found():
    # An unknown order id / half-expired session lands on the account order
    # list; that must be a clear OrderNotFound, not a generic ParseFailed.
    from app.scrapers.base import OrderNotFound

    html = (
        "<html><body><div class='account-index-transport'>"
        "<ul class='information'></ul></div></body></html>"
    )
    with pytest.raises(OrderNotFound):
        BanggoodScraper().parse(html, "116598360")
