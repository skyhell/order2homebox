"""Render QR labels for the Brother QL-500 on DK-22211 (29 mm endless).

The printable width of 29 mm endless tape is exactly 306 px at 300 dpi.
Default layout: two identical QR codes side by side across the width
(cut apart by hand — the QL-500 has no auto-cutter), each with the
Homebox asset ID underneath.
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
    font = _font()

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
