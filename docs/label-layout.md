# Label layout (Brother QL-500, DK-22211)

![Example label](label-example.png)

## Dimensions

| Property | Value |
| --- | --- |
| Tape | DK-22211, 29 mm endless film |
| Printer resolution | 300 dpi |
| Printable width | **306 px** (fixed — the renderer always outputs exactly this) |
| Label length | dynamic, ≈ 178 px ≈ 15 mm with default settings |
| QR codes per row | 2 (default via `O2H_LABEL_QR_PER_ROW`, 1–3; per item on the edit page) |
| QR content | `{O2H_HOMEBOX_PUBLIC_URL or O2H_HOMEBOX_URL}/a/{asset_id}` |
| Error correction | M |
| Quiet zone | 2 modules |
| Asset ID text | off by default — `O2H_LABEL_SHOW_ASSET_ID` is only the preset; every item card and every print button in the UI has its own checkbox |

Both QR codes on one row are identical: cut the strip in half to get two labels
for the same asset (e.g. one on the box, one on the part). The QL-500 has no
auto-cutter — use the built-in manual cutter lever.

The renderer lives in `server/app/labels.py`; the module scale is always an
integer so QR modules map 1:1 onto printer dots (no dithering artifacts).

### How many codes per row

Every item card and the *Print label* page offer *three small labels instead of
two*, for small parts you have several of. What that costs, measured with a
39-character QR URL
(`https://box.example.org/a/000-629`, version 3, 29 modules):

| Per row | Cell | Module scale | Code | Label length |
| --- | --- | --- | --- | --- |
| 1 | 306 px | 8 px = 0.68 mm | 19.6 mm | 336 px ≈ 32 mm |
| 2 | 153 px | 4 px = 0.34 mm | 9.8 mm | 162 px ≈ 15 mm |
| 3 | 102 px | 2 px = 0.17 mm | 4.9 mm | 133 px ≈ 13 mm |

Three per row is at the edge of what a phone camera reads. Confirmed working on
real DK-22211 tape with an iPhone 13 Pro, so 0.17 mm modules are not a
theoretical size — but that is one camera on fresh tape, which is why the UI
still says to try one before relying on it. The integer module scale is what
makes this a cliff rather than a slope: 90 px of usable box divided by 33
modules gives 2, and 2.7 is not available.

If a three-up code ever stops scanning, the lever is the error correction level:
`error="m"` in `_qr_image` down to `"l"` fits a 39-character URL into version 2
(25 modules), which lifts the scale to 3 px ≈ 0.25 mm — at the cost of tolerance
for scratched or dirty labels.

The asset ID is **not** printed at three per row. A cell is 102 px and an id
like `12345-678` is 121 px at the default size — it would print straight across
the neighbouring code and make it unscannable. Both pages untick and disable the
id checkbox (`applyThreeUp` in `app.js` is the one definition), `/print` refuses
the combination, and the renderer shrinks the font to fit as a last resort,
because `O2H_LABEL_QR_PER_ROW=3` can still be combined with
`O2H_LABEL_SHOW_ASSET_ID=1` in `.env`.

## Text labels

The *Text label* page prints one or two lines and no QR code, for things that
are not Homebox items. Same 306 px width; the length again follows the content.

| Property | Value |
| --- | --- |
| Lines | 1 or 2 (`TEXT_MAX_LINES`), max 60 characters each |
| Type size | derived, not configured — each line is grown until it spans the width |
| Height cap | 110 px ≈ 10 mm per line (`TEXT_MAX_LINE_HEIGHT`) |
| Legibility floor | 30 px ≈ 2.8 mm (`TEXT_MIN_LINE_HEIGHT`, only in *keep the height* mode) |
| Margins | 12 px at both ends of the width, 12 px top/bottom, 12 px between lines |

Every line is fitted **on its own**, so a short line prints larger than a long
one next to it. The cap is what stops a two-letter label from printing letters
3 cm tall across a hand's length of tape; the line height is measured from the
ink, not from the font metrics, so unused ascender space is not printed as
blank tape.

Because the text runs *across* the 29 mm width, a long line necessarily comes
out small — around 15 characters still print about 3 mm tall, 40 characters
about 1 mm. Short labels are what this is for.

### What a second line costs (two checkboxes on the page)

Three modes, `HEIGHT_GROW` / `HEIGHT_KEEP` / `HEIGHT_FORCE` in `labels.py`,
picked by two nested checkboxes:

| Mode | Checkboxes | A second line |
| --- | --- | --- |
| `grow` | both off (default) | makes the label longer, both lines full size |
| `keep` | *keep the label height* | shares the room of one line, but never goes below the legibility floor |
| `force` | plus *length over legibility* | shares the room whatever the type size ends up being |

The trade-off behind the switch is worth understanding before using it: the
type size follows from the **width** today, so pinning the height means the
text can no longer span it. Measured, in px of tape (÷ 10.55 for mm):

| Text | one line | `grow` | `keep` | `force` |
| --- | --- | --- | --- | --- |
| `A4` / `Sechskant M4` | 134 | 176 | 115 | 115 |
| `Werkstatt` / `Regal 3` | 70 | 156 | 95 | 70 |
| `Schrauben` / `M4 x 20 mm` | 65 | 112 | 96 | 64 |
| `Verzinkte Sechskantschrauben M4` / `Karton 12` | 36 | 93 | 78 | 50 |

`keep` and `force` are identical wherever the floor never bites (first row).
Where it does, `force` buys the tape with type as small as 1.3 mm — readable up
close, not from across the room.

Three properties worth relying on: neither mode makes a label *longer* than
`grow` would (the fit is bounded, never stretched), `force` is never longer
than `keep`, and neither does anything at all to a single-line label.

`force` cannot always reach the single-line length: `_fit_line` will not go
below `TEXT_MIN_SIZE`, so a first line that is already tiny leaves nothing to
halve — the last row above ends at 50 px rather than 36 px. Below that size
nothing is readable anyway.
