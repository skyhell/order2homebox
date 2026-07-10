"""Symmetric encryption for secrets stored in .env (e.g. the Homebox password).

The app needs these secrets in clear text at runtime (to log into the Homebox
API), so they cannot be one-way hashed like the web-login password. Instead we
encrypt them with a Fernet key kept in a SEPARATE key file (``data/secret.key``,
chmod 600) — never in .env itself. That way a leaked/committed/backed-up .env
alone does not reveal the password.

Caveat: this is protection against accidental disclosure, not against an
attacker who already has read access to the container's filesystem — such an
attacker can read the key file too. Real defence there needs OS-level secret
sealing (e.g. systemd LoadCredentialEncrypted / a TPM).

Values are stored with an ``enc:`` prefix. ``decrypt_maybe`` returns any value
without that prefix unchanged, so existing plain-text installs keep working.
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ENC_PREFIX = "enc:"


class SecretError(Exception):
    """Raised when an encrypted value cannot be decrypted."""


def _resolve_key_path(key_path: Path | None) -> Path:
    if key_path is not None:
        return key_path
    # Imported lazily; only used by callers outside Settings construction
    # (the config validator passes key_path explicitly to avoid this).
    from .config import settings

    return settings.data_dir / "secret.key"


def get_or_create_key(key_path: Path | None = None) -> bytes:
    """Return the Fernet key, generating and persisting it (chmod 600) if the
    key file does not exist yet."""
    path = _resolve_key_path(key_path)
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Create with restrictive permissions from the start (umask-independent).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def encrypt(plaintext: str, key_path: Path | None = None) -> str:
    """Encrypt a secret, returning the ``enc:`` token to place in .env."""
    token = Fernet(get_or_create_key(key_path)).encrypt(plaintext.encode("utf-8"))
    return ENC_PREFIX + token.decode("ascii")


def decrypt_maybe(value: str, key_path: Path | None = None) -> str:
    """Decrypt an ``enc:`` value; return anything else (plain text) unchanged."""
    if not value or not value.startswith(ENC_PREFIX):
        return value
    path = _resolve_key_path(key_path)
    if not path.exists():
        raise SecretError(
            f"An 'enc:' secret is set in .env but the key file ({path}) is "
            "missing. Restore it, or re-encrypt the secret with: "
            "python -m app.encrypt"
        )
    token = value[len(ENC_PREFIX):].encode("ascii")
    try:
        return Fernet(path.read_bytes().strip()).decrypt(token).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretError(
            f"Could not decrypt an 'enc:' secret from .env — the key file "
            f"({path}) does not match. Re-encrypt with: python -m app.encrypt"
        ) from exc
