"""HTTP client for the print agent running on the Raspberry Pi."""
import httpx

from .config import settings


class PrintError(Exception):
    """User-facing print failure."""


async def print_png(png: bytes, copies: int = 1) -> dict:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{settings.print_agent_url.rstrip('/')}/print",
                files={"file": ("label.png", png, "image/png")},
                data={"copies": str(copies)},
                headers={"X-Api-Key": settings.print_agent_api_key},
            )
    except httpx.HTTPError as exc:
        raise PrintError(f"Print agent unreachable: {exc}") from exc
    if r.status_code != 200:
        raise PrintError(f"Print agent error (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


async def health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.print_agent_url.rstrip('/')}/health")
        r.raise_for_status()
        return {"ok": True, **r.json()}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
