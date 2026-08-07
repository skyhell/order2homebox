"""Turn the plain-text secrets in an existing .env into ``enc:`` values.

Fresh installs get encrypted secrets from ``install/install-in-lxc.sh``, and
``update.sh`` deliberately never touches .env — so an installation older than
that automation keeps its plain-text password forever. This is the one-off
migration for those, run through ``install/encrypt-env.sh``:

    python -m app.encrypt_env [path/to/.env]

Everything the file does not need to change stays byte-identical: comments,
blank lines, key order, unknown keys. Running it twice is a no-op, because a
value that already starts with ``enc:`` is skipped.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from .secrets import ENC_PREFIX, SecretError, decrypt_maybe, encrypt

# Exactly the settings Settings._decrypt_secrets() runs through decrypt_maybe
# (see config.py). Encrypting anything else would make the app read the token
# as if it were the value.
ENCRYPTED_KEYS = ("O2H_HOMEBOX_PASSWORD", "O2H_PRINT_AGENT_API_KEY")


def _key_of(line: str) -> str:
    """The variable a .env line assigns to, or "" for comments and blanks."""
    body = line.strip()
    if not body or body.startswith("#") or "=" not in body:
        return ""
    if body.startswith("export "):
        body = body[len("export ") :].lstrip()
    key = body.split("=", 1)[0].strip()  # split once: a value may contain "="
    return key if key.isidentifier() else ""


def migrate_text(text: str, encrypt_fn=encrypt) -> tuple[str, list[str]]:
    """Rewrite the secret lines of a .env. Returns the new text and the keys
    that changed.

    The *values* are read with python-dotenv rather than parsed here, because
    that is the parser pydantic-settings itself uses: it decides what quoting
    and escaping mean. Encrypting our own guess of the value instead would
    silently store the wrong password — quotes and all — and the Homebox login
    would start failing with a perfectly valid-looking ``enc:`` value in .env.
    """
    values = dotenv_values(stream=io.StringIO(text))
    lines = text.splitlines(keepends=True)
    changed: list[str] = []
    for i, line in enumerate(lines):
        key = _key_of(line)
        if key not in ENCRYPTED_KEYS:
            continue
        value = values.get(key) or ""
        if not value or value.startswith(ENC_PREFIX):
            continue  # nothing set, or already encrypted
        newline = line[len(line.rstrip("\r\n")) :]
        # The token is URL-safe base64, so it never needs quoting.
        lines[i] = f"{key}={encrypt_fn(value)}{newline}"
        changed.append(key)
    return "".join(lines), changed


def _verify(original: str, migrated: str, changed: list[str], key_path: Path) -> None:
    """Read the rewritten file back the way the app will and prove every
    changed secret still decrypts to what it was. Raises SecretError if not."""
    before = dotenv_values(stream=io.StringIO(original))
    after = dotenv_values(stream=io.StringIO(migrated))
    for key in changed:
        if decrypt_maybe(after.get(key) or "", key_path) != before.get(key):
            raise SecretError(
                f"{key} would not survive the migration — .env was left "
                "untouched. Please report this with the shape of the line "
                "(never the value)."
            )


def key_path_for(env_path: Path, values: dict) -> Path:
    """Where the app will look for the Fernet key, given this .env.

    Mirrors config.py: ``data_dir`` defaults to the relative ``data`` and the
    service's WorkingDirectory is the directory holding .env, so a relative
    path resolves against that directory — not against the caller's cwd.
    """
    data_dir = Path(values.get("O2H_DATA_DIR") or "data")
    if not data_dir.is_absolute():
        data_dir = env_path.resolve().parent / data_dir
    return data_dir / "secret.key"


def _write_atomically(path: Path, text: str) -> None:
    """Replace the file in one step, keeping its permissions.

    A half-written .env would leave the service unable to start, so the new
    content lands in a temporary file next to it (same filesystem) and is then
    moved into place.
    """
    tmp = path.with_name(path.name + ".tmp")
    mode = path.stat().st_mode & 0o777
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        tmp.chmod(mode)
    except OSError:
        pass
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    env_path = Path(args[0] if args else ".env")
    if not env_path.exists():
        print(f"{env_path} does not exist.", file=sys.stderr)
        return 1

    original = env_path.read_text(encoding="utf-8")
    values = dotenv_values(stream=io.StringIO(original))
    key_path = key_path_for(env_path, values)

    migrated, changed = migrate_text(
        original, lambda value: encrypt(value, key_path)
    )
    if not changed:
        # ASCII only, here and below: this runs after the file has been
        # written, and a console that cannot encode the character would turn a
        # successful migration into a traceback.
        print(f"Nothing to do - no plain-text secrets left in {env_path}.")
        return 0

    try:
        _verify(original, migrated, changed, key_path)
    except SecretError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    backup = env_path.with_name(env_path.name + ".bak")
    backup.write_text(original, encoding="utf-8")
    try:
        backup.chmod(env_path.stat().st_mode & 0o777)
    except OSError:
        pass
    _write_atomically(env_path, migrated)

    # Names only — printing a value would put the secret in the terminal
    # scrollback and the journal, which is what this whole exercise is about.
    print(f"Encrypted: {', '.join(changed)}")
    print(f"Key file:  {key_path}")
    print(f"Backup:    {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
