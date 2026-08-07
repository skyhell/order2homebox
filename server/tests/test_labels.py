import pytest
from PIL import ImageChops

from app.labels import (
    LABEL_WIDTH,
    TEXT_MARGIN_X,
    TEXT_MARGIN_Y,
    TEXT_LINE_GAP,
    TEXT_MAX_CHARS,
    TEXT_MAX_LINE_HEIGHT,
    TEXT_MIN_LINE_HEIGHT,
    _qr_image,
    clean_text_line,
    render_label,
    render_label_png,
    render_text_label,
    render_text_label_png,
)

QR_URL = "http://homebox.test/a/000-123"


def _ink(image):
    """Bounding box of the black pixels — what actually lands on the tape."""
    return ImageChops.invert(image).getbbox()


def test_label_width_is_exactly_306px():
    label = render_label("000-123", QR_URL)
    assert label.width == LABEL_WIDTH == 306


def test_two_identical_qr_codes_side_by_side():
    label = render_label("000-123", QR_URL, show_asset_id=False, qr_per_row=2)
    cell = LABEL_WIDTH // 2
    left = label.crop((0, 0, cell, label.height))
    right = label.crop((cell, 0, 2 * cell, label.height))
    assert left.tobytes() == right.tobytes()
    # and the cells actually contain black QR modules
    assert min(left.tobytes()) == 0


def test_qr_matches_reference_rendering():
    """The pasted QR must be exactly what segno renders for the asset URL."""
    label = render_label("000-123", QR_URL, show_asset_id=False, qr_per_row=2)
    cell = LABEL_WIDTH // 2
    qr = _qr_image(QR_URL, cell - 12)
    x = (cell - qr.width) // 2
    region = label.crop((x, 6, x + qr.width, 6 + qr.height))
    assert region.tobytes() == qr.tobytes()


def test_asset_id_text_adds_height():
    with_text = render_label("000-123", QR_URL, show_asset_id=True)
    without_text = render_label("000-123", QR_URL, show_asset_id=False)
    assert with_text.height > without_text.height
    assert with_text.width == without_text.width == LABEL_WIDTH


def test_single_qr_layout():
    label = render_label("000-123", QR_URL, show_asset_id=False, qr_per_row=1)
    assert label.width == LABEL_WIDTH
    assert min(label.tobytes()) == 0


def test_render_is_deterministic_png():
    a = render_label_png("000-123", QR_URL)
    b = render_label_png("000-123", QR_URL)
    assert a == b and a.startswith(b"\x89PNG")


# -- text-only labels ---------------------------------------------------------


def test_text_label_has_the_same_fixed_width():
    assert render_text_label(["Schrauben"]).width == LABEL_WIDTH == 306


def test_text_spans_the_label_width():
    """The whole point: the type size follows from the text, so a line ends up
    as large as the 29 mm tape allows instead of at some configured size."""
    box = _ink(render_text_label(["Schrauben M4"]))
    usable = LABEL_WIDTH - 2 * TEXT_MARGIN_X
    assert box[0] >= TEXT_MARGIN_X - 2  # margin kept at both ends
    assert box[2] <= LABEL_WIDTH - TEXT_MARGIN_X + 2
    # within one integer font step of the full usable width
    assert box[2] - box[0] >= usable - 12


def test_a_long_line_still_fits():
    box = _ink(render_text_label(["Verzinkte Sechskantschrauben M4 x 20 mm"]))
    assert box[2] - box[0] <= LABEL_WIDTH - 2 * TEXT_MARGIN_X + 2


def test_second_line_makes_the_label_longer():
    one = render_text_label(["Schrauben"])
    two = render_text_label(["Schrauben", "M4 x 20 mm"])
    assert two.height > one.height
    assert two.width == one.width == LABEL_WIDTH


def test_each_line_is_fitted_on_its_own():
    """A short line must not be shrunk to the size a long line needs — both
    lines span the width, which is what makes a two-line label readable."""
    solo = render_text_label(["A4"])
    solo_box = _ink(solo)
    pair = render_text_label(["A4", "Verzinkte Sechskantschrauben M4"])
    top = pair.crop((0, 0, LABEL_WIDTH, solo_box[3] + 1))
    assert _ink(top) == solo_box


def test_a_short_text_does_not_eat_the_tape():
    """Without the height cap, a two-letter label would print letters 3 cm tall
    across a hand's length of endless tape."""
    label = render_text_label(["A"])
    assert label.height <= 2 * TEXT_MARGIN_Y + TEXT_MAX_LINE_HEIGHT


def test_blank_lines_are_dropped():
    assert render_text_label_png(["", "Kabel"]) == render_text_label_png(["Kabel"])
    assert render_text_label_png(["Kabel", "  "]) == render_text_label_png(["Kabel"])


def test_only_two_lines_are_printed():
    assert render_text_label_png(["a", "b", "c"]) == render_text_label_png(["a", "b"])


def test_text_without_any_content_is_refused():
    with pytest.raises(ValueError):
        render_text_label(["", "   "])


def test_clean_text_line_normalizes_input():
    assert clean_text_line("  Schrauben   M4 \n x20 ") == "Schrauben M4 x20"
    assert clean_text_line(None) == ""
    assert len(clean_text_line("x" * 200)) == TEXT_MAX_CHARS


def test_keep_height_puts_two_lines_in_the_room_of_one():
    """The point of the mode: a second line costs no extra tape."""
    one = render_text_label(["A4"])
    two = render_text_label(["A4", "Sechskant M4"], keep_height=True)
    assert two.height <= one.height
    # and it really is smaller type, not a clipped line
    assert _ink(two)[3] - _ink(two)[1] < _ink(one)[3] - _ink(one)[1]


def test_keep_height_never_makes_a_label_longer():
    """The cap is only ever an upper bound, so the mode cannot backfire — a
    'save tape' switch that sometimes costs tape would be worse than useless."""
    for lines in (
        ["Schrauben", "M4 x 20 mm"],
        ["A4", "Sechskant M4"],
        ["Werkstatt", "Regal 3"],
        ["Verzinkte Sechskantschrauben M4", "Karton 12"],
    ):
        normal = render_text_label(lines)
        kept = render_text_label(lines, keep_height=True)
        assert kept.height <= normal.height, lines


def test_keep_height_stops_at_a_readable_size():
    """Two long lines cannot both shrink into the room of one without becoming
    unreadable — there the label is allowed to grow instead."""
    lines = ["Schrauben", "M4 x 20 mm"]
    kept = render_text_label(lines, keep_height=True)
    per_line = (kept.height - 2 * TEXT_MARGIN_Y - TEXT_LINE_GAP) / 2
    assert per_line >= TEXT_MIN_LINE_HEIGHT
    # strictly equal height would have been ~14 px per line, i.e. 1.3 mm
    assert kept.height > render_text_label(["Schrauben"]).height


def test_keep_height_changes_nothing_for_a_single_line():
    assert render_text_label_png(["Werkstatt"], keep_height=True) == (
        render_text_label_png(["Werkstatt"])
    )


def test_text_render_is_deterministic_png():
    a = render_text_label_png(["Kabel", "USB-C"])
    b = render_text_label_png(["Kabel", "USB-C"])
    assert a == b and a.startswith(b"\x89PNG")
