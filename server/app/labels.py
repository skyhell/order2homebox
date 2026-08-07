"""Render labels for the Brother QL-500 on DK-22211 (29 mm endless).

The printable width of 29 mm endless tape is exactly 306 px at 300 dpi.
Default QR layout: two identical QR codes side by side across the width
(cut apart by hand — the QL-500 has no auto-cutter), each with the
Homebox asset ID underneath.

``render_text_label`` produces the second kind: plain text, no QR code, for
labelling things that are not Homebox items.
"""
from io import BytesIO
from pathlib import Path

import segno
from PIL import Image, ImageDraw, ImageFont

LABEL_WIDTH = 306  # 29 mm endless @ 300 dpi — fixed by the printer
QUIET_ZONE_MODULES = 2  # printed on white tape, 2 modules suffice
CELL_PADDING = 6  # px around each QR cell
TEXT_HEIGHT = 34  # px reserved for the asset-ID line
FONT_SIZE = 26

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _font(size: int = FONT_SIZE) -> ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    try:
        # No system font: the built-in one must still follow the requested size,
        # otherwise a text label would come out at one fixed tiny size.
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 cannot size the built-in font
        return ImageFont.load_default()


def _qr_image(content: str, box_px: int) -> Image.Image:
    """Render a QR code as large as fits into box_px (integer module scale)."""
    qr = segno.make(content, error="m")
    modules = qr.symbol_size(scale=1, border=0)[0]
    total_modules = modules + 2 * QUIET_ZONE_MODULES
    scale = max(2, box_px // total_modules)
    buf = BytesIO()
    qr.save(buf, kind="png", scale=scale, border=QUIET_ZONE_MODULES)
    buf.seek(0)
    return Image.open(buf).convert("L")


def _asset_id_font(draw: ImageDraw.ImageDraw, asset_id: str, max_width: int):
    """A font size at which the id stays inside its own cell.

    At three codes per row a cell is 102 px and ``12345-678`` is 121 px at the
    default size — it would print straight across the neighbouring QR code and
    make it unscannable. The UI does not offer the id at three per row, but
    ``O2H_LABEL_QR_PER_ROW`` can still ask for both.
    """
    size = FONT_SIZE
    font = _font(size)
    while size > 8 and draw.textlength(asset_id, font=font) > max_width:
        size -= 1
        font = _font(size)
    return font


def render_label(
    asset_id: str,
    qr_content: str,
    show_asset_id: bool = True,
    qr_per_row: int = 2,
) -> Image.Image:
    qr_per_row = max(1, min(qr_per_row, 3))
    cell_width = LABEL_WIDTH // qr_per_row
    qr_img = _qr_image(qr_content, cell_width - 2 * CELL_PADDING)

    text_height = TEXT_HEIGHT if show_asset_id else 0
    height = CELL_PADDING + qr_img.height + text_height + CELL_PADDING
    label = Image.new("L", (LABEL_WIDTH, height), 255)
    draw = ImageDraw.Draw(label)
    font = _asset_id_font(draw, asset_id, cell_width - 2 * CELL_PADDING)

    for i in range(qr_per_row):
        cell_x = i * cell_width
        label.paste(qr_img, (cell_x + (cell_width - qr_img.width) // 2, CELL_PADDING))
        if show_asset_id:
            text_width = draw.textlength(asset_id, font=font)
            draw.text(
                (cell_x + (cell_width - text_width) / 2, CELL_PADDING + qr_img.height + 2),
                asset_id,
                fill=0,
                font=font,
            )
    return label


def render_label_png(
    asset_id: str,
    qr_content: str,
    show_asset_id: bool = True,
    qr_per_row: int = 2,
) -> bytes:
    image = render_label(asset_id, qr_content, show_asset_id, qr_per_row)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# -- text-only labels ---------------------------------------------------------

TEXT_MAX_LINES = 2
TEXT_MAX_CHARS = 60  # per line; beyond this the letters get too small to read
TEXT_MARGIN_X = 12  # px kept clear at both ends of the tape width
TEXT_MARGIN_Y = 12  # px above the first line and below the last
TEXT_LINE_GAP = 12  # px between the two lines
# A line is grown until it hits the tape width — or this. Without the cap a
# two-letter label would print letters 3 cm tall and eat a hand's length of
# tape; 110 px is roughly 10 mm, still readable across a workshop.
TEXT_MAX_LINE_HEIGHT = 110
# Floor for the "keep the height" mode below. Squeezing two lines into the room
# of one is worth tape, but not at any price: ~2.8 mm is about where a label
# stops being readable at arm's length, so below this the label grows instead.
TEXT_MIN_LINE_HEIGHT = 30
TEXT_MIN_SIZE = 8
TEXT_MAX_SIZE = 400

# What a second line does to the length of the label.
HEIGHT_GROW = "grow"  # it makes the label longer, in full-size type (default)
HEIGHT_KEEP = "keep"  # both lines share the room one would have taken, but the
#                       label still grows rather than fall below the floor
HEIGHT_FORCE = "force"  # same room, whatever it costs in legibility
HEIGHT_MODES = (HEIGHT_GROW, HEIGHT_KEEP, HEIGHT_FORCE)


def clean_text_line(raw: str) -> str:
    """Normalize one line of user input: no line breaks, no runs of spaces."""
    return " ".join((raw or "").split())[:TEXT_MAX_CHARS]


def _fit_line(text: str, max_width: int, max_height: int):
    """Largest font that keeps ``text`` inside the box, plus its ink box.

    The size is derived, not configured: a text label is meant to be read from
    a distance, so every line is grown until it spans the tape width. Binary
    search because loading a TrueType font per candidate size is not free.
    """
    lo, hi = TEXT_MIN_SIZE, TEXT_MAX_SIZE
    font = _font(lo)
    box = font.getbbox(text)
    while lo <= hi:
        size = (lo + hi) // 2
        candidate = _font(size)
        candidate_box = candidate.getbbox(text)
        fits = (
            candidate_box[2] - candidate_box[0] <= max_width
            and candidate_box[3] - candidate_box[1] <= max_height
        )
        if fits:
            font, box, lo = candidate, candidate_box, size + 1
        else:
            hi = size - 1
    return font, box


def _line_cap(lines: list[str], max_width: int, height_mode: str) -> int:
    """How tall a single line may be.

    Normally the full cap. In the other two modes a second line does not make
    the label longer: both lines share the room the first one alone would have
    taken. That trade is real, not free — the size follows from the width
    today, so pinning the height means the text no longer spans it.

    HEIGHT_KEEP stops the trade at the legibility floor and lets the label grow
    after all; HEIGHT_FORCE keeps the length whatever it costs to read.
    """
    if height_mode == HEIGHT_GROW or len(lines) < 2:
        return TEXT_MAX_LINE_HEIGHT
    _, box = _fit_line(lines[0], max_width, TEXT_MAX_LINE_HEIGHT)
    single_line_height = box[3] - box[1]
    share = (single_line_height - TEXT_LINE_GAP) // 2
    if height_mode == HEIGHT_FORCE:
        return max(1, share)
    return max(TEXT_MIN_LINE_HEIGHT, share)


def render_text_label(
    lines: list[str], height_mode: str = HEIGHT_GROW
) -> Image.Image:
    """One or two lines of text across the tape width, no QR code.

    Each line is fitted on its own, so both really do span the width — a short
    line ends up in bigger letters than a long one. The label is only as long
    as the text needs, because the tape is endless and every millimetre saved
    is tape not thrown away.

    ``height_mode`` decides what a second line costs: see HEIGHT_GROW /
    HEIGHT_KEEP / HEIGHT_FORCE above. Since the cap is only ever an upper
    bound, neither of the latter two can produce a label longer than the same
    text with HEIGHT_GROW.
    """
    lines = [line for line in (clean_text_line(x) for x in lines) if line]
    lines = lines[:TEXT_MAX_LINES]
    if not lines:
        raise ValueError("a text label needs at least one non-empty line")
    if height_mode not in HEIGHT_MODES:
        height_mode = HEIGHT_GROW

    max_width = LABEL_WIDTH - 2 * TEXT_MARGIN_X
    cap = _line_cap(lines, max_width, height_mode)
    fitted = [_fit_line(line, max_width, cap) for line in lines]
    # The ink box, not the font metrics: ascender/descender space the text does
    # not use would be printed as blank tape.
    heights = [box[3] - box[1] for _, box in fitted]
    height = (
        2 * TEXT_MARGIN_Y + sum(heights) + TEXT_LINE_GAP * (len(lines) - 1)
    )

    label = Image.new("L", (LABEL_WIDTH, height), 255)
    draw = ImageDraw.Draw(label)
    y = TEXT_MARGIN_Y
    for line, (font, box), line_height in zip(lines, fitted, heights):
        # Subtract the box offsets so the ink itself is centred and its top
        # edge sits exactly on y.
        x = (LABEL_WIDTH - (box[2] - box[0])) / 2 - box[0]
        draw.text((x, y - box[1]), line, fill=0, font=font)
        y += line_height + TEXT_LINE_GAP
    return label


def render_text_label_png(lines: list[str], height_mode: str = HEIGHT_GROW) -> bytes:
    buf = BytesIO()
    render_text_label(lines, height_mode).save(buf, format="PNG")
    return buf.getvalue()
