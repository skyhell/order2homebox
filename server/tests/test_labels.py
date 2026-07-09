from app.labels import LABEL_WIDTH, _qr_image, render_label, render_label_png

QR_URL = "http://homebox.test/a/000-123"


def test_label_width_is_exactly_306px():
    label = render_label("000-123", QR_URL)
    assert label.width == LABEL_WIDTH == 306


def test_two_identical_qr_codes_side_by_side():
    label = render_label("000-123", QR_URL, show_asset_id=False, qr_per_row=2)
    cell = LABEL_WIDTH // 2
    left = label.crop((0, 0, cell, label.height))
    right = label.crop((cell, 0, 2 * cell, label.height))
    assert list(left.getdata()) == list(right.getdata())
    # and the cells actually contain black QR modules
    assert min(left.getdata()) == 0


def test_qr_matches_reference_rendering():
    """The pasted QR must be exactly what segno renders for the asset URL."""
    label = render_label("000-123", QR_URL, show_asset_id=False, qr_per_row=2)
    cell = LABEL_WIDTH // 2
    qr = _qr_image(QR_URL, cell - 12)
    x = (cell - qr.width) // 2
    region = label.crop((x, 6, x + qr.width, 6 + qr.height))
    assert list(region.getdata()) == list(qr.getdata())


def test_asset_id_text_adds_height():
    with_text = render_label("000-123", QR_URL, show_asset_id=True)
    without_text = render_label("000-123", QR_URL, show_asset_id=False)
    assert with_text.height > without_text.height
    assert with_text.width == without_text.width == LABEL_WIDTH


def test_single_qr_layout():
    label = render_label("000-123", QR_URL, show_asset_id=False, qr_per_row=1)
    assert label.width == LABEL_WIDTH
    assert min(label.getdata()) == 0


def test_render_is_deterministic_png():
    a = render_label_png("000-123", QR_URL)
    b = render_label_png("000-123", QR_URL)
    assert a == b and a.startswith(b"\x89PNG")
