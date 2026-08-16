"""HTTP client for a print agent running on a Raspberry Pi.

Which agent is never guessed here: there can be several of them, and the one to
use belongs to the browser that asked (see ``agents.selected``). So every call
carries its agent.
"""
import httpx

from .agents import Agent


class PrintError(Exception):
    """User-facing print failure."""


# Not a failure of any agent but the absence of one — stored like an error so a
# card that got no label still says why, and read back through error_key().
NO_PRINTER = "No print agent configured"


def error_key(message: str) -> str:
    """The locale key naming the cause behind a print failure, or "" when the
    message says nothing we recognise.

    The agent hands the operating system's words through verbatim, and the two
    everyday causes arrive as a missing device file (printer off or unplugged)
    and as no answer at all (the Pi is down). Both are read here rather than
    stored: an error written into the draft weeks ago should still turn into a
    sentence, and into the language the page is being read in.
    """
    if message == NO_PRINTER:
        return "err_no_printer"
    if "No such file or directory" in message and "/dev/" in message:
        return "err_printer_offline"
    if "unreachable" in message:
        return "err_agent_unreachable"
    return ""


async def print_png(agent: Agent, png: bytes, copies: int = 1) -> dict:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{agent.base}/print",
                files={"file": ("label.png", png, "image/png")},
                data={"copies": str(copies)},
                headers={"X-Api-Key": agent.api_key},
            )
    except httpx.HTTPError as exc:
        raise PrintError(f"Print agent unreachable: {exc}") from exc
    if r.status_code != 200:
        raise PrintError(f"Print agent error (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


async def shutdown(agent: Agent) -> dict:
    """Ask the agent to power its Pi down. The box may drop the connection
    while going down, which is a success, not an error."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{agent.base}/shutdown",
                headers={"X-Api-Key": agent.api_key},
            )
    except httpx.HTTPError as exc:
        raise PrintError(f"Print agent unreachable: {exc}") from exc
    if r.status_code != 200:
        raise PrintError(f"Print agent error (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()


async def health(agent: Agent) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{agent.base}/health")
        r.raise_for_status()
        return {"ok": True, **r.json()}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
