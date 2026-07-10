"""Encrypt a secret for storage in .env (e.g. O2H_HOMEBOX_PASSWORD).

Usage:  python -m app.encrypt [secret]
Reads the secret from stdin (hidden) when no argument is given, creates the
key file (data/secret.key) if needed, and prints the ``enc:`` token to paste
into .env. See app/secrets.py for the threat model.
"""
import getpass
import sys

from .secrets import encrypt


def main() -> None:
    if len(sys.argv) > 1:
        secret = sys.argv[1]
    else:
        secret = getpass.getpass("Secret: ")
    print(encrypt(secret))


if __name__ == "__main__":
    main()
