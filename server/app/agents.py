"""The print agents (one Raspberry Pi with one QL-500 each) and which of them a
browser prints on.

There can be several Pis — workshop, office — and they are kept in
``data/agents.json`` rather than in ``.env``, so a new one is added on the
settings page instead of in a shell. The .env values are the starting point: as
long as no file exists they *are* the list, which is what every installation
older than this feature keeps running on.

Which agent to print on is not a property of the installation but of the desk
you are sitting at, so it lives in a cookie of that browser — the same way the
language does.
"""
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request

from .config import settings
from .secrets import SecretError, decrypt_maybe, encrypt

PRINTER_COOKIE = "o2h_printer"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
NAME_MAX = 40
DEFAULT_ID = "default"


class AgentError(Exception):
    """Rejected printer data — the message is a locale key."""


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    url: str
    api_key: str

    @property
    def base(self) -> str:
        return self.url.rstrip("/")


def _path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "agents.json"


def _slug(name: str) -> str:
    """A name turned into something that survives a URL and a cookie. German
    names are the normal case here, so umlauts are spelled out instead of
    becoming dashes — „Büro" is buero, not b-ro."""
    text = name.lower()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, plain)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug[:32] or "printer"


def _unique_id(name: str, taken: set[str]) -> str:
    base = _slug(name)
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _env_agent() -> Agent | None:
    """The single agent from .env — the whole list until a second one is added."""
    if not settings.print_agent_url:
        return None
    host = urlparse(settings.print_agent_url).hostname or settings.print_agent_url
    return Agent(
        id=DEFAULT_ID,
        name=host,
        url=settings.print_agent_url,
        api_key=settings.print_agent_api_key,
    )


def _read_file() -> list[Agent] | None:
    """The stored list, or None when there is no readable file — which is not an
    error but the state every installation starts in."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    agents = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("url"):
            continue
        try:
            api_key = decrypt_maybe(str(entry.get("api_key", "")))
        except SecretError:
            # The key file is gone; the agent is still worth listing, its row
            # will simply fail to print and say so.
            api_key = ""
        agents.append(
            Agent(
                id=str(entry["id"]),
                name=str(entry.get("name") or entry["id"]),
                url=str(entry["url"]),
                api_key=api_key,
            )
        )
    return agents


def load() -> list[Agent]:
    stored = _read_file()
    if stored is not None:
        return stored
    env = _env_agent()
    return [env] if env else []


def by_id(agent_id: str) -> Agent | None:
    for agent in load():
        if agent.id == agent_id:
            return agent
    return None


def selected(request: Request, agents: list[Agent] | None = None) -> Agent | None:
    """The agent this browser prints on: its cookie, or the first one. An id
    from a printer that has since been removed falls back the same way. Callers
    holding the list already pass it in rather than reading the file twice."""
    agents = load() if agents is None else agents
    if not agents:
        return None
    wanted = request.cookies.get(PRINTER_COOKIE, "")
    for agent in agents:
        if agent.id == wanted:
            return agent
    return agents[0]


def save(agent_id: str, name: str, url: str, api_key: str) -> Agent:
    """Add a printer (empty agent_id) or change one. An empty key on a change
    keeps the stored one — the key is never sent to the browser, so it cannot be
    sent back either."""
    name = name.strip()[:NAME_MAX]
    url = url.strip().rstrip("/")
    if not name:
        raise AgentError("err_printer_name")
    if not url.startswith(("http://", "https://")) or not urlparse(url).hostname:
        raise AgentError("err_printer_url")
    agents = load()
    if not agent_id:
        agent = Agent(
            id=_unique_id(name, {a.id for a in agents}),
            name=name,
            url=url,
            api_key=api_key.strip(),
        )
        agents.append(agent)
        _write(agents)
        return agent
    for i, existing in enumerate(agents):
        if existing.id != agent_id:
            continue
        agent = Agent(
            id=existing.id,
            name=name,
            url=url,
            api_key=api_key.strip() or existing.api_key,
        )
        agents[i] = agent
        _write(agents)
        return agent
    raise AgentError("err_printer_unknown")


def remove(agent_id: str) -> None:
    """Drop a printer. The last one stays: without it printing has no target at
    all, and a wrong address is changed, not deleted."""
    agents = load()
    if not any(agent.id == agent_id for agent in agents):
        raise AgentError("err_printer_unknown")
    if len(agents) < 2:
        raise AgentError("err_last_printer")
    _write([agent for agent in agents if agent.id != agent_id])


def _write(agents: list[Agent]) -> None:
    data = {
        "agents": [
            {
                "id": agent.id,
                "name": agent.name,
                "url": agent.url,
                # Encrypted with the same key file as the .env secrets, so a
                # copied data directory does not hand the keys over.
                "api_key": encrypt(agent.api_key) if agent.api_key else "",
            }
            for agent in agents
        ]
    }
    path = _path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
