# Label layout (Brother QL-500, DK-22211)

![Example label](label-example.png)

## Dimensions

| Property | Value |
| --- | --- |
| Tape | DK-22211, 29 mm endless film |
| Printer resolution | 300 dpi |
| Printable width | **306 px** (fixed — the renderer always outputs exactly this) |
| Label length | dynamic, ≈ 178 px ≈ 15 mm with default settings |
| QR codes per row | 2 (configurable via `O2H_LABEL_QR_PER_ROW`, 1–3) |
| QR content | `{O2H_HOMEBOX_PUBLIC_URL or O2H_HOMEBOX_URL}/a/{asset_id}` |
| Error correction | M |
| Quiet zone | 2 modules |
| Asset ID text | optional — `O2H_LABEL_SHOW_ASSET_ID` is only the default; every item card and every print button in the UI has its own checkbox |

Both QR codes on one row are identical: cut the strip in half to get two labels
for the same asset (e.g. one on the box, one on the part). The QL-500 has no
auto-cutter — use the built-in manual cutter lever.

The renderer lives in `server/app/labels.py`; the module scale is always an
integer so QR modules map 1:1 onto printer dots (no dithering artifacts).

## Text labels

The *Text label* page prints one or two lines and no QR code, for things that
are not Homebox items. Same 306 px width; the length again follows the content.

| Property | Value |
| --- | --- |
| Lines | 1 or 2 (`TEXT_MAX_LINES`), max 60 characters each |
| Type size | derived, not configured — each line is grown until it spans the width |
| Height cap | 110 px ≈ 10 mm per line (`TEXT_MAX_LINE_HEIGHT`) |
| Margins | 12 px at both ends of the width, 12 px top/bottom, 12 px between lines |

Every line is fitted **on its own**, so a short line prints larger than a long
one next to it. The cap is what stops a two-letter label from printing letters
3 cm tall across a hand's length of tape; the line height is measured from the
ink, not from the font metrics, so unused ascender space is not printed as
blank tape.

Because the text runs *across* the 29 mm width, a long line necessarily comes
out small — around 15 characters still print about 3 mm tall, 40 characters
about 1 mm. Short labels are what this is for.
