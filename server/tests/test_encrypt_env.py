"""Migrating an existing .env to enc: values.

The risk this covers is not the crypto — it is rewriting the file: a value that
comes back different after the migration means a Homebox login that fails with
a perfectly valid-looking enc: token in .env.
"""
import io
import os

import pytest
from dotenv import dotenv_values

# Imported here, not inside a test: app.config builds a Settings() at import
# time from the current directory, and the CLI tests chdir into a temp dir
# holding a freshly migrated .env whose key file the test environment does not
# point at. Importing first pins it to the conftest environment.
from app.config import Settings
from app.encrypt_env import ENCRYPTED_KEYS, main, migrate_text
from app.secrets import ENC_PREFIX, decrypt_maybe, encrypt

# What install-in-lxc.sh writes, plus the comments a hand-edited file has.
SAMPLE = """\
# order2homebox configuration
O2H_HOMEBOX_URL=http://homebox.lan:7745
O2H_HOMEBOX_PUBLIC_URL=
O2H_HOMEBOX_USERNAME=me@example.com
O2H_HOMEBOX_PASSWORD=hunter2
O2H_PRINT_AGENT_URL=http://homeboxprint:8010
O2H_PRINT_AGENT_API_KEY=abc123
O2H_WEB_USER=admin
O2H_WEB_PASSWORD_HASH=$2b$12$notarealhash
O2H_SECRET_KEY=deadbeef
O2H_DEFAULT_LANGUAGE=de
"""


def _encrypter(tmp_path):
    key = tmp_path / "secret.key"
    return key, lambda value: encrypt(value, key_path=key)


def _values(text):
    return dotenv_values(stream=io.StringIO(text))


def test_both_secrets_are_encrypted_and_decrypt_back(tmp_path):
    key, enc = _encrypter(tmp_path)
    out, changed = migrate_text(SAMPLE, enc)
    assert sorted(changed) == sorted(ENCRYPTED_KEYS)
    values = _values(out)
    assert decrypt_maybe(values["O2H_HOMEBOX_PASSWORD"], key_path=key) == "hunter2"
    assert decrypt_maybe(values["O2H_PRINT_AGENT_API_KEY"], key_path=key) == "abc123"
    assert "hunter2" not in out and "abc123" not in out


def test_everything_else_is_left_byte_identical(tmp_path):
    _, enc = _encrypter(tmp_path)
    out, _ = migrate_text(SAMPLE, enc)
    before = SAMPLE.splitlines()
    after = out.splitlines()
    assert len(before) == len(after)
    for old, new in zip(before, after):
        if old.split("=", 1)[0] in ENCRYPTED_KEYS:
            continue
        assert old == new  # comments, order, hash, secret key, blank lines


def test_running_it_twice_changes_nothing(tmp_path):
    _, enc = _encrypter(tmp_path)
    once, _ = migrate_text(SAMPLE, enc)
    twice, changed = migrate_text(once, enc)
    assert changed == []
    assert twice == once


def test_a_quoted_password_keeps_its_real_value(tmp_path):
    """python-dotenv reads O2H_HOMEBOX_PASSWORD="pa ss" as `pa ss`. Encrypting
    the quotes along with it would store a password nobody typed."""
    key, enc = _encrypter(tmp_path)
    out, _ = migrate_text('O2H_HOMEBOX_PASSWORD="pa ss"\n', enc)
    token = _values(out)["O2H_HOMEBOX_PASSWORD"]
    assert decrypt_maybe(token, key_path=key) == "pa ss"


@pytest.mark.parametrize(
    "quoted",
    [
        '"with=equals"',
        '"with spaces"',
        '"ümlaut-ß"',
        r'"a\"quote"',
        "'single\"quoted'",
        '"#notacomment"',
        "plain-no-quotes",
    ],
)
def test_awkward_passwords_survive(tmp_path, quoted):
    key, enc = _encrypter(tmp_path)
    source = f"O2H_HOMEBOX_PASSWORD={quoted}\n"
    original = _values(source)["O2H_HOMEBOX_PASSWORD"]
    out, _ = migrate_text(source, enc)
    token = _values(out)["O2H_HOMEBOX_PASSWORD"]
    assert decrypt_maybe(token, key_path=key) == original


def test_a_line_the_parser_cannot_read_is_left_alone(tmp_path):
    """Unbalanced quotes: python-dotenv gives up on the line. Guessing at the
    value there is exactly how a password gets silently mangled — leave it and
    let the operator see that nothing was encrypted."""
    _, enc = _encrypter(tmp_path)
    source = 'O2H_HOMEBOX_PASSWORD="quote\'and"both"\n'
    out, changed = migrate_text(source, enc)
    assert changed == []
    assert out == source


def test_an_empty_value_is_left_alone_and_creates_no_key(tmp_path):
    """A Pi that was never set up has no API key — that must not turn into an
    enc: token wrapping the empty string, and must not create a key file."""
    key, enc = _encrypter(tmp_path)
    out, changed = migrate_text("O2H_PRINT_AGENT_API_KEY=\n", enc)
    assert changed == [] and out == "O2H_PRINT_AGENT_API_KEY=\n"
    assert not key.exists()


def test_unrelated_keys_are_never_touched(tmp_path):
    """Encrypting anything Settings does not decrypt would hand the app the
    token as if it were the value."""
    _, enc = _encrypter(tmp_path)
    out, changed = migrate_text("O2H_SECRET_KEY=deadbeef\nO2H_WEB_USER=admin\n", enc)
    assert changed == []
    assert out == "O2H_SECRET_KEY=deadbeef\nO2H_WEB_USER=admin\n"


def test_crlf_line_endings_are_preserved(tmp_path):
    _, enc = _encrypter(tmp_path)
    out, changed = migrate_text("O2H_HOMEBOX_PASSWORD=hunter2\r\nO2H_WEB_USER=a\r\n", enc)
    assert changed == ["O2H_HOMEBOX_PASSWORD"]
    assert out.endswith("O2H_WEB_USER=a\r\n")
    assert out.splitlines(keepends=True)[0].endswith("\r\n")


# -- the CLI ------------------------------------------------------------------


def _run(env_file, monkeypatch, tmp_path):
    """Run main() with data_dir pointing next to the .env, like the service."""
    monkeypatch.chdir(tmp_path)
    return main([str(env_file)])


def test_cli_rewrites_the_file_and_leaves_a_backup(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(SAMPLE, encoding="utf-8")
    assert _run(env_file, monkeypatch, tmp_path) == 0

    migrated = env_file.read_text(encoding="utf-8")
    assert _values(migrated)["O2H_HOMEBOX_PASSWORD"].startswith(ENC_PREFIX)
    assert (tmp_path / ".env.bak").read_text(encoding="utf-8") == SAMPLE
    assert (tmp_path / "data" / "secret.key").exists()  # relative to the .env

    out = capsys.readouterr().out
    assert "hunter2" not in out and "abc123" not in out  # names only, never values


def test_cli_decrypts_to_the_original_through_settings(tmp_path, monkeypatch):
    """End to end: after the migration the app must see the same password it
    saw before."""
    env_file = tmp_path / ".env"
    env_file.write_text(SAMPLE, encoding="utf-8")
    _run(env_file, monkeypatch, tmp_path)

    for name in ("O2H_HOMEBOX_PASSWORD", "O2H_PRINT_AGENT_API_KEY", "O2H_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("O2H_DATA_DIR", str(tmp_path / "data"))
    settings = Settings(_env_file=env_file)
    assert settings.homebox_password == "hunter2"
    assert settings.print_agent_api_key == "abc123"


def test_cli_is_idempotent(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(SAMPLE, encoding="utf-8")
    _run(env_file, monkeypatch, tmp_path)
    after_first = env_file.read_text(encoding="utf-8")

    assert _run(env_file, monkeypatch, tmp_path) == 0
    assert env_file.read_text(encoding="utf-8") == after_first
    assert "Nothing to do" in capsys.readouterr().out


def test_cli_keeps_the_file_permissions(tmp_path, monkeypatch):
    """.env is chmod 600 — a migration that widens it would undo the point."""
    env_file = tmp_path / ".env"
    env_file.write_text(SAMPLE, encoding="utf-8")
    env_file.chmod(0o600)
    _run(env_file, monkeypatch, tmp_path)
    if os.name == "posix":  # chmod is a no-op on Windows
        assert (env_file.stat().st_mode & 0o777) == 0o600
        assert ((tmp_path / ".env.bak").stat().st_mode & 0o777) == 0o600


def test_cli_reports_a_missing_file(tmp_path, monkeypatch, capsys):
    assert _run(tmp_path / "nope.env", monkeypatch, tmp_path) == 1
    assert "does not exist" in capsys.readouterr().err


def test_cli_leaves_the_file_alone_when_verification_fails(tmp_path, monkeypatch):
    """The safety net: if a rewritten secret would not decrypt back, .env must
    stay as it was and no backup must pretend a migration happened."""
    import app.encrypt_env as module

    env_file = tmp_path / ".env"
    env_file.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(
        module, "migrate_text", lambda text, fn: (text.replace("hunter2", "enc:junk"), ["O2H_HOMEBOX_PASSWORD"])
    )
    assert _run(env_file, monkeypatch, tmp_path) == 1
    assert env_file.read_text(encoding="utf-8") == SAMPLE
    assert not (tmp_path / ".env.bak").exists()
