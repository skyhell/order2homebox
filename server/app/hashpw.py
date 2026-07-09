"""Generate a bcrypt hash for O2H_WEB_PASSWORD_HASH.

Usage:  python -m app.hashpw [password]
Reads the password from stdin (hidden) when no argument is given.
"""
import getpass
import sys

import bcrypt


def main() -> None:
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("Password: ")
    print(bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii"))


if __name__ == "__main__":
    main()
