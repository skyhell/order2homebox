"""Encrypt a secret for storage in .env (e.g. O2H_HOMEBOX_PASSWORD).

Usage:  python -m app.encrypt [secret]
        python -m app.encrypt --stdin < file

Reads the secret from the terminal (hidden) when no argument is given, creates
the key file (data/secret.key) if needed, and prints the ``enc:`` token to paste
into .env. See app/secrets.py for the threat model.

To convert the secrets of an EXISTING .env in place, use app.encrypt_env.
"""
import getpass
import sys

from .secrets import encrypt


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--stdin":
        # For scripts: an argument would be visible in the process list for as
        # long as the call runs, and land in the shell history.
        secret = sys.stdin.read().rstrip("\n")
    elif args:
        secret = args[0]
    else:
        secret = getpass.getpass("Secret: ")
    print(encrypt(secret))


if __name__ == "__main__":
    main()
