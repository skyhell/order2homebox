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
| Asset ID text | optional (`O2H_LABEL_SHOW_ASSET_ID`, per-print checkbox in the UI) |

Both QR codes on one row are identical: cut the strip in half to get two labels
for the same asset (e.g. one on the box, one on the part). The QL-500 has no
auto-cutter — use the built-in manual cutter lever.

The renderer lives in `server/app/labels.py`; the module scale is always an
integer so QR modules map 1:1 onto printer dots (no dithering artifacts).
