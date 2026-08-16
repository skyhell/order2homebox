"""The list of print agents and the printer a browser prints on."""
import json
import os

import pytest
from starlette.requests import Request

from app import agents
from app.config import settings


@pytest.fixture(autouse=True)
def clean_list():
    """Every test starts from "no file yet" — the state a fresh install is in —
    and leaves nothing behind for the web tests, which expect the .env agent."""
    path = settings.data_dir / "agents.json"
    path.unlink(missing_ok=True)
    yield
    path.unlink(missing_ok=True)


def _request(cookie: str = "") -> Request:
    headers = [(b"cookie", f"{agents.PRINTER_COOKIE}={cookie}".encode())] if cookie else []
    return Request({"type": "http", "headers": headers})


def test_without_a_file_the_env_agent_is_the_whole_list():
    """Every installation older than this feature has its Pi in .env only, and
    must keep printing without anyone touching the settings page."""
    listed = agents.load()
    assert [a.id for a in listed] == [agents.DEFAULT_ID]
    assert listed[0].url == settings.print_agent_url


def test_adding_the_second_printer_keeps_the_first():
    """The .env agent is not lost the moment the list becomes a file."""
    agents.save("", "Büro", "http://pi-buero:8010", "key-2")
    listed = agents.load()
    assert [a.id for a in listed] == [agents.DEFAULT_ID, "buero"]
    assert listed[0].url == settings.print_agent_url
    assert listed[1].api_key == "key-2"


def test_the_api_key_is_stored_encrypted():
    agents.save("", "Werkstatt", "http://pi-werkstatt:8010", "sesame")
    path = settings.data_dir / "agents.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    stored = [entry for entry in raw["agents"] if entry["id"] == "werkstatt"][0]
    assert stored["api_key"].startswith("enc:")
    assert "sesame" not in path.read_text(encoding="utf-8")
    assert agents.by_id("werkstatt").api_key == "sesame"
    if os.name == "posix":
        assert oct(path.stat().st_mode)[-3:] == "600"


def test_changing_a_printer_without_a_key_keeps_the_stored_one():
    """The key is never rendered back into the form, so an empty field means
    "unchanged" — anything else would wipe it on every rename."""
    added = agents.save("", "Büro", "http://pi-buero:8010", "key-2")
    agents.save(added.id, "Büro oben", "http://pi-buero:8011", "")
    changed = agents.by_id(added.id)
    assert (changed.name, changed.url) == ("Büro oben", "http://pi-buero:8011")
    assert changed.api_key == "key-2"


def test_a_name_and_a_reachable_looking_address_are_required():
    with pytest.raises(agents.AgentError, match="err_printer_name"):
        agents.save("", "  ", "http://pi:8010", "k")
    with pytest.raises(agents.AgentError, match="err_printer_url"):
        agents.save("", "Büro", "pi-buero:8010", "k")
    assert len(agents.load()) == 1  # nothing written


def test_two_printers_of_the_same_name_get_their_own_id():
    first = agents.save("", "Büro", "http://pi-a:8010", "k")
    second = agents.save("", "Büro", "http://pi-b:8010", "k")
    assert first.id != second.id
    assert len(agents.load()) == 3


def test_the_last_printer_stays():
    """Without one, printing has no target at all — a wrong address is changed,
    not deleted."""
    with pytest.raises(agents.AgentError, match="err_last_printer"):
        agents.remove(agents.DEFAULT_ID)
    added = agents.save("", "Büro", "http://pi-buero:8010", "k")
    agents.remove(agents.DEFAULT_ID)
    assert [a.id for a in agents.load()] == [added.id]


def test_the_cookie_decides_which_printer_prints():
    added = agents.save("", "Büro", "http://pi-buero:8010", "k")
    assert agents.selected(_request(added.id)).id == added.id
    assert agents.selected(_request()).id == agents.DEFAULT_ID


def test_a_cookie_naming_a_removed_printer_falls_back():
    """The choice lives in a browser, the list on the server — one can name a
    printer the other no longer has."""
    assert agents.selected(_request("gone")).id == agents.DEFAULT_ID


def test_an_empty_list_is_an_answer_too():
    agents._write([])
    assert agents.load() == []
    assert agents.selected(_request()) is None
