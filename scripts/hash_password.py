#!/usr/bin/env python3
from __future__ import annotations

from getpass import getpass

from argon2 import PasswordHasher


def main() -> int:
    first = getpass("STROY owner password: ")
    second = getpass("Repeat password: ")
    if not first:
        raise SystemExit("Password must not be empty")
    if first != second:
        raise SystemExit("Passwords do not match")
    print(PasswordHasher().hash(first))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
