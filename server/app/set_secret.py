"""Set one encrypted secret in .env, e.g. after changing the Homebox password.

    python -m app.set_secret O2H_HOMEBOX_PASSWORD [path/to/.env]

Asks for the value twice without echoing it, checks that it actually works,
encrypts it with the existing key file and replaces the line — so the secret
never passes through the shell history or the process list, and .env keeps
everything else byte-identical.

Unlike app.encrypt_env this leaves no ``.bak``: the old file would hold the
previous secret, and the point of running this is usually that the previous
secret should stop existing. The replacement is still atomic, so a failed write
cannot leave a half-written .env behind.
"""
from __future__ import annotations

import asyncio
import getpass
import io
import sys
from pathlib import Path

from dotenv import dotenv_values

from .encrypt_env import ENCRYPTED_KEYS, key_of, key_path_for, write_atomically
from .secrets import decrypt_maybe, encrypt

USAGE = "usage: python -m app.set_secret <KEY> [path/to/.env] [--stdin] [--no-verify]"


def replace_value(text: str, key: str, value: str) -> tuple[str, bool]:
    """Set ``key`` to ``value``, appending the line if it is not there yet.

    Every occurrence is replaced, not just the last one python-dotenv would
    win with: a leftover earlier line would keep the old secret in the file
    while the app happily uses the new one.
    """
    lines = text.splitlines(keepends=True)
    replaced = False
    for i, line in enumerate(lines):
        if key_of(line) != key:
            continue
        newline = line[len(line.rstrip("\r\n")) :]
        lines[i] = f"{key}={value}{newline}"
        replaced = True
    if replaced:
        return "".join(lines), True

    # Not in the file: append, matching the line ending already in use.
    newline = "\n"
    if lines:
        last = lines[-1]
        newline = last[len(last.rstrip("\r\n")) :] or "\n"
        if not lines[-1].endswith(("\n", "\r")):
            lines[-1] = lines[-1] + newline
    lines.append(f"{key}={value}{newline}")
    return "".join(lines), False


async def _homebox_rejects(url: str, username: str, password: str) -> str:
    """Empty string if the credentials log into Homebox, otherwise the reason.

    Deliberately not via HomeboxClient: importing it builds the global Settings,
    which decrypts the value currently in .env. That is exactly what is broken
    when someone reaches for this tool — a lost key file or a mangled ``enc:``
    value — and the fix must not require the broken thing to load first. Keep
    the request in step with HomeboxClient._login in homebox.py.
    """
    import httpx

    if not url:
        return "no O2H_HOMEBOX_URL in this .env, so there is nothing to check against"
    try:
        async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=15.0) as client:
            r = await client.post(
                "/api/v1/users/login",
                json={
                    "username": username,
                    "password": password,
                    "stayLoggedIn": True,
                },
            )
    except httpx.HTTPError as exc:
        return f"Homebox unreachable: {exc}"
    if r.status_code != 200:
        return f"Homebox rejected these credentials (HTTP {r.status_code})"
    if not r.json().get("token"):
        return "Homebox accepted the login but returned no token"
    return ""


def _ask() -> str:
    """Read the new secret twice, without echoing it."""
    first = getpass.getpass("New secret: ")
    if not first.strip():
        print("Empty - nothing changed.", file=sys.stderr)
        return ""
    if first != getpass.getpass("Repeat: "):
        print("The two entries differ - nothing changed.", file=sys.stderr)
        return ""
    return first


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    from_stdin = "--stdin" in args
    verify = "--no-verify" not in args
    args = [a for a in args if not a.startswith("--")]
    if not args:
        print(USAGE, file=sys.stderr)
        return 2

    key = args[0]
    if key not in ENCRYPTED_KEYS:
        print(
            f"{key} is not one of the encrypted settings "
            f"({', '.join(ENCRYPTED_KEYS)}). Anything else would be handed to "
            "the app as the token instead of the value.",
            file=sys.stderr,
        )
        return 2

    env_path = Path(args[1] if len(args) > 1 else ".env")
    if not env_path.exists():
        print(f"{env_path} does not exist.", file=sys.stderr)
        return 1

    original = env_path.read_text(encoding="utf-8")
    values = dotenv_values(stream=io.StringIO(original))

    secret = sys.stdin.read().rstrip("\n") if from_stdin else _ask()
    if not secret:
        return 1

    if verify and key == "O2H_HOMEBOX_PASSWORD":
        # Before writing, not after: a wrong password written to .env would take
        # the app down at the next restart, and the mistake is far harder to
        # spot from the journal than from here. The URL and user come from the
        # file being edited, so this tests the combination that will be live.
        print("Checking it against Homebox ...")
        reason = asyncio.run(
            _homebox_rejects(
                values.get("O2H_HOMEBOX_URL") or "",
                values.get("O2H_HOMEBOX_USERNAME") or "",
                secret,
            )
        )
        if reason:
            print(f"ERROR: {reason}", file=sys.stderr)
            print(
                f"{env_path} was NOT changed. Use --no-verify to set it anyway "
                "(e.g. while Homebox is down).",
                file=sys.stderr,
            )
            return 1
        print("Homebox accepted it.")

    key_path = key_path_for(env_path, values)
    token = encrypt(secret, key_path)
    updated, replaced = replace_value(original, key, token)

    # Same safety net as the migration: prove the app will read back what was
    # typed before the file is touched.
    written = dotenv_values(stream=io.StringIO(updated))
    if decrypt_maybe(written.get(key) or "", key_path) != secret:
        print(
            f"ERROR: {key} would not read back correctly - {env_path} was left "
            "untouched.",
            file=sys.stderr,
        )
        return 1

    write_atomically(env_path, updated)
    print(f"{'Replaced' if replaced else 'Added'} {key} in {env_path} (encrypted).")
    print("Restart the service for it to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
