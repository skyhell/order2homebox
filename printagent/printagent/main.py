"""Print agent for order2homebox — runs on the Raspberry Pi with the QL-500.

Receives a PNG and prints it via brother_ql on 29 mm endless tape (DK-22211).
Configuration via environment variables:

  O2H_PRINT_API_KEY   shared secret; requests must send it as X-Api-Key
  O2H_PRINTER_MODEL   default QL-500
  O2H_LABEL_TYPE      default 29 (29 mm endless)
  O2H_PRINTER_DEVICE  default /dev/usb/lp0
  O2H_PRINTER_BACKEND default linux_kernel
  O2H_DRY_RUN         set to 1 to write PNGs to disk instead of printing
  O2H_DRY_RUN_DIR     where dry-run PNGs go (default: current directory)

brother_ql is imported lazily so the agent can be developed on machines
without the printer stack installed (dry-run mode).
"""
import os
import time
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from PIL import Image

API_KEY = os.environ.get("O2H_PRINT_API_KEY", "")
PRINTER_MODEL = os.environ.get("O2H_PRINTER_MODEL", "QL-500")
LABEL_TYPE = os.environ.get("O2H_LABEL_TYPE", "29")
PRINTER_DEVICE = os.environ.get("O2H_PRINTER_DEVICE", "/dev/usb/lp0")
PRINTER_BACKEND = os.environ.get("O2H_PRINTER_BACKEND", "linux_kernel")
DRY_RUN = os.environ.get("O2H_DRY_RUN", "").lower() not in ("", "0", "false")
DRY_RUN_DIR = Path(os.environ.get("O2H_DRY_RUN_DIR", "."))

MAX_COPIES = 20

app = FastAPI(title="order2homebox print agent")


def _check_api_key(key: str) -> None:
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid API key")


@app.get("/health")
def health() -> dict:
    printer_ok = DRY_RUN or os.access(PRINTER_DEVICE, os.W_OK)
    return {
        "status": "ok" if printer_ok else "printer_unavailable",
        "dry_run": DRY_RUN,
        "device": PRINTER_DEVICE,
        "model": PRINTER_MODEL,
        "label": LABEL_TYPE,
    }


@app.post("/print")
async def print_label(
    file: UploadFile = File(...),
    copies: int = Form(1),
    x_api_key: str = Header(default=""),
) -> dict:
    _check_api_key(x_api_key)
    copies = max(1, min(copies, MAX_COPIES))
    data = await file.read()
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"invalid image: {exc}") from exc

    if DRY_RUN:
        DRY_RUN_DIR.mkdir(parents=True, exist_ok=True)
        out = DRY_RUN_DIR / f"label-{int(time.time() * 1000)}.dryrun.png"
        image.save(out)
        return {"status": "dry_run", "copies": copies, "file": str(out)}

    from brother_ql.backends.helpers import send
    from brother_ql.conversion import convert
    from brother_ql.raster import BrotherQLRaster

    qlr = BrotherQLRaster(PRINTER_MODEL)
    instructions = convert(
        qlr=qlr,
        images=[image] * copies,
        label=LABEL_TYPE,
        rotate="0",  # server renders exactly 306 px wide for 29 mm endless
        threshold=70.0,
        dither=False,
        compress=False,
        red=False,
        dpi_600=False,
        hq=False,      # QL-500 has no HQ mode
        cut=False,     # QL-500 has no auto-cutter
    )
    try:
        send(
            instructions=instructions,
            printer_identifier=f"file://{PRINTER_DEVICE}",
            backend_identifier=PRINTER_BACKEND,
            blocking=True,
        )
    except Exception as exc:  # brother_ql raises plain Exceptions/OSErrors
        raise HTTPException(status_code=502, detail=f"printer error: {exc}") from exc
    return {"status": "printed", "copies": copies}
