import pytest

from app.secrets import ENC_PREFIX, SecretError, decrypt_maybe, encrypt


def test_encrypt_decrypt_roundtrip(tmp_path):
    key = tmp_path / "secret.key"
    token = encrypt("hunter2", key_path=key)
    assert token.startswith(ENC_PREFIX)
    assert "hunter2" not in token  # ciphertext, not plain text
    assert decrypt_maybe(token, key_path=key) == "hunter2"


def test_key_file_created_with_600_perms(tmp_path):
    key = tmp_path / "secret.key"
    encrypt("x", key_path=key)
    assert key.exists()
    # On POSIX the mode must be owner-only; on Windows chmod is a no-op, skip.
    import os

    if os.name == "posix":
        assert (key.stat().st_mode & 0o777) == 0o600


def test_plaintext_passes_through_unchanged(tmp_path):
    """Existing installs store the password in plain text — it must be returned
    as-is without needing a key file."""
    key = tmp_path / "missing.key"
    assert decrypt_maybe("plain-password", key_path=key) == "plain-password"
    assert not key.exists()


def test_empty_value_passes_through(tmp_path):
    assert decrypt_maybe("", key_path=tmp_path / "k") == ""


def test_missing_key_file_raises_clear_error(tmp_path):
    with pytest.raises(SecretError, match="key file"):
        decrypt_maybe(ENC_PREFIX + "gAAAAAB_bogus", key_path=tmp_path / "absent.key")


def test_wrong_key_raises(tmp_path):
    token = encrypt("secret", key_path=tmp_path / "a.key")
    from cryptography.fernet import Fernet

    (tmp_path / "b.key").write_bytes(Fernet.generate_key())
    with pytest.raises(SecretError):
        decrypt_maybe(token, key_path=tmp_path / "b.key")


def test_settings_decrypts_homebox_password(tmp_path, monkeypatch):
    """A Settings instance must expose the decrypted password when the .env
    value is an enc: token."""
    from app.config import Settings

    key = tmp_path / "secret.key"
    token = encrypt("live-homebox-pw", key_path=key)
    monkeypatch.setenv("O2H_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("O2H_HOMEBOX_PASSWORD", token)
    settings = Settings(_env_file=None)
    assert settings.homebox_password == "live-homebox-pw"
