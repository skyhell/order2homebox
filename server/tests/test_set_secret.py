"""Setting a single encrypted secret in .env (app.set_secret).

The failure that matters here is a wrong value reaching .env: the service then
starts but cannot log into Homebox, and the reason is far easier to see at the
prompt than in the journal. So the checks happen before anything is written.
"""
import io
import os

import pytest
from dotenv import dotenv_values

from app.config import Settings  # see the note in test_encrypt_env.py
from app.set_secret import main, replace_value
from app.secrets import ENC_PREFIX, decrypt_maybe

# The agent key is left empty on purpose: a made-up enc: value would be a token
# no key file can decrypt, and building Settings from this file is how the CLI
# tests prove the app reads the new password back.
SAMPLE = """\
# order2homebox configuration
O2H_HOMEBOX_URL=http://homebox.lan:7745
O2H_HOMEBOX_PASSWORD=enc:old-token-here
O2H_PRINT_AGENT_API_KEY=
O2H_WEB_USER=admin
"""


def _values(text):
    return dotenv_values(stream=io.StringIO(text))


# -- replace_value ------------------------------------------------------------


def test_replaces_the_line_and_leaves_the_rest_alone():
    out, replaced = replace_value(SAMPLE, "O2H_HOMEBOX_PASSWORD", "enc:new")
    assert replaced
    assert _values(out)["O2H_HOMEBOX_PASSWORD"] == "enc:new"
    assert "old-token-here" not in out
    for line in SAMPLE.splitlines():
        if not line.startswith("O2H_HOMEBOX_PASSWORD"):
            assert line in out


def test_appends_when_the_key_is_missing():
    out, replaced = replace_value("O2H_WEB_USER=admin\n", "O2H_HOMEBOX_PASSWORD", "enc:x")
    assert not replaced
    assert out == "O2H_WEB_USER=admin\nO2H_HOMEBOX_PASSWORD=enc:x\n"


def test_appends_to_a_file_without_a_trailing_newline():
    out, _ = replace_value("O2H_WEB_USER=admin", "O2H_HOMEBOX_PASSWORD", "enc:x")
    assert out == "O2H_WEB_USER=admin\nO2H_HOMEBOX_PASSWORD=enc:x\n"


def test_every_duplicate_line_is_replaced():
    """python-dotenv takes the last one, so replacing only that would leave the
    previous secret sitting in the file."""
    text = "O2H_HOMEBOX_PASSWORD=first\nO2H_WEB_USER=a\nO2H_HOMEBOX_PASSWORD=second\n"
    out, _ = replace_value(text, "O2H_HOMEBOX_PASSWORD", "enc:new")
    assert "first" not in out and "second" not in out
    assert out.count("enc:new") == 2


def test_crlf_is_preserved():
    out, _ = replace_value("O2H_HOMEBOX_PASSWORD=a\r\n", "O2H_HOMEBOX_PASSWORD", "enc:x")
    assert out == "O2H_HOMEBOX_PASSWORD=enc:x\r\n"


# -- the CLI ------------------------------------------------------------------


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return path


def _answers(monkeypatch, *replies):
    """Feed getpass, so the prompts can be driven from a test."""
    queue = list(replies)
    monkeypatch.setattr("app.set_secret.getpass.getpass", lambda _: queue.pop(0))


def _homebox_accepts(monkeypatch, reason=""):
    async def fake(url, username, password):
        return reason

    monkeypatch.setattr("app.set_secret._homebox_rejects", fake)


def test_sets_a_new_password_the_app_can_read(env_file, monkeypatch):
    _answers(monkeypatch, "n3w-pass=word", "n3w-pass=word")
    _homebox_accepts(monkeypatch)
    assert main(["O2H_HOMEBOX_PASSWORD", str(env_file)]) == 0

    text = env_file.read_text(encoding="utf-8")
    assert _values(text)["O2H_HOMEBOX_PASSWORD"].startswith(ENC_PREFIX)
    assert "n3w-pass=word" not in text  # never written in the clear

    for name in ("O2H_HOMEBOX_PASSWORD", "O2H_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("O2H_DATA_DIR", str(env_file.parent / "data"))
    assert Settings(_env_file=env_file).homebox_password == "n3w-pass=word"


def test_the_agent_key_is_not_checked_against_homebox(env_file, monkeypatch):
    """Only the Homebox password can be tested that way; the key must still be
    settable."""
    _answers(monkeypatch, "pi-key", "pi-key")
    monkeypatch.setattr(
        "app.set_secret._homebox_rejects",
        lambda *a: pytest.fail("must not check the agent key against Homebox"),
    )
    assert main(["O2H_PRINT_AGENT_API_KEY", str(env_file)]) == 0
    key_path = env_file.parent / "data" / "secret.key"
    token = _values(env_file.read_text(encoding="utf-8"))["O2H_PRINT_AGENT_API_KEY"]
    assert decrypt_maybe(token, key_path=key_path) == "pi-key"


def test_a_password_homebox_rejects_is_not_written(env_file, monkeypatch):
    _answers(monkeypatch, "wrong", "wrong")
    _homebox_accepts(monkeypatch, reason="Homebox login failed (HTTP 401)")
    assert main(["O2H_HOMEBOX_PASSWORD", str(env_file)]) == 1
    assert env_file.read_text(encoding="utf-8") == SAMPLE


def test_no_verify_writes_it_anyway(env_file, monkeypatch):
    """Homebox being down must not stop you from setting the password."""
    _answers(monkeypatch, "set-blind", "set-blind")
    monkeypatch.setattr(
        "app.set_secret._homebox_rejects",
        lambda *a: pytest.fail("--no-verify must not contact Homebox"),
    )
    assert main(["O2H_HOMEBOX_PASSWORD", str(env_file), "--no-verify"]) == 0
    assert env_file.read_text(encoding="utf-8") != SAMPLE


def test_mistyped_repeat_changes_nothing(env_file, monkeypatch):
    _answers(monkeypatch, "one", "other")
    assert main(["O2H_HOMEBOX_PASSWORD", str(env_file)]) == 1
    assert env_file.read_text(encoding="utf-8") == SAMPLE


def test_an_empty_password_changes_nothing(env_file, monkeypatch):
    _answers(monkeypatch, "   ")
    assert main(["O2H_HOMEBOX_PASSWORD", str(env_file)]) == 1
    assert env_file.read_text(encoding="utf-8") == SAMPLE


def test_refuses_a_setting_that_is_never_decrypted(env_file, monkeypatch):
    """Encrypting e.g. O2H_SECRET_KEY would hand the app the token as the
    value, with no hint as to why sessions stopped working."""
    assert main(["O2H_SECRET_KEY", str(env_file)]) == 2
    assert env_file.read_text(encoding="utf-8") == SAMPLE


def test_reports_a_missing_env_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["O2H_HOMEBOX_PASSWORD", str(tmp_path / "nope.env")]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_stdin_mode_skips_the_prompt(env_file, monkeypatch):
    """For scripting; the interactive path is what the shell script uses."""
    _homebox_accepts(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO("piped-pass\n"))
    assert main(["O2H_HOMEBOX_PASSWORD", str(env_file), "--stdin"]) == 0
    key_path = env_file.parent / "data" / "secret.key"
    token = _values(env_file.read_text(encoding="utf-8"))["O2H_HOMEBOX_PASSWORD"]
    assert decrypt_maybe(token, key_path=key_path) == "piped-pass"


def test_keeps_the_file_permissions(env_file, monkeypatch):
    env_file.chmod(0o600)
    _answers(monkeypatch, "x", "x")
    _homebox_accepts(monkeypatch)
    main(["O2H_HOMEBOX_PASSWORD", str(env_file)])
    if os.name == "posix":  # chmod is a no-op on Windows
        assert (env_file.stat().st_mode & 0o777) == 0o600


def test_works_when_the_current_env_cannot_be_decrypted(tmp_path, monkeypatch, capsys):
    """The recovery case, and the reason the Homebox check does not go through
    HomeboxClient: with a lost key file the current enc: value is undecryptable,
    so importing the app's settings blows up — in the one situation where this
    tool is the fix. It has to run anyway."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "O2H_HOMEBOX_URL=http://homebox.lan:7745\n"
        "O2H_HOMEBOX_PASSWORD=enc:token-no-key-file-can-decrypt\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _answers(monkeypatch, "rescue-pw", "rescue-pw")
    _homebox_accepts(monkeypatch)

    assert main(["O2H_HOMEBOX_PASSWORD", str(env_file)]) == 0
    token = _values(env_file.read_text(encoding="utf-8"))["O2H_HOMEBOX_PASSWORD"]
    assert decrypt_maybe(token, key_path=tmp_path / "data" / "secret.key") == "rescue-pw"


def test_a_verify_failure_names_the_reason(env_file, monkeypatch, capsys):
    _answers(monkeypatch, "wrong", "wrong")
    _homebox_accepts(monkeypatch, reason="Homebox unreachable: connection refused")
    main(["O2H_HOMEBOX_PASSWORD", str(env_file)])
    err = capsys.readouterr().err
    assert "connection refused" in err
    assert "--no-verify" in err  # tells you how to proceed anyway
    assert "wrong" not in err  # but never echoes the password


def test_leaves_no_backup_holding_the_old_secret(env_file, monkeypatch):
    """Unlike the one-off migration: a .bak here would preserve exactly the
    secret that is being retired."""
    _answers(monkeypatch, "x", "x")
    _homebox_accepts(monkeypatch)
    main(["O2H_HOMEBOX_PASSWORD", str(env_file)])
    assert not (env_file.parent / ".env.bak").exists()
