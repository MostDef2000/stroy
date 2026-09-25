from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def random_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_worker_token(candidate: str, configured_token: str, configured_hash: str) -> bool:
    if configured_hash:
        return hmac.compare_digest(sha256_text(candidate), configured_hash)
    return bool(configured_token) and hmac.compare_digest(candidate, configured_token)


@dataclass
class _Attempt:
    failures: int = 0
    blocked_until: float = 0.0


class LoginThrottle:
    """Small single-process throttle. Replaceable with Redis without changing auth routes."""

    def __init__(self, max_delay_seconds: int = 30) -> None:
        self._attempts: dict[str, _Attempt] = {}
        self._max_delay = max_delay_seconds

    def retry_after(self, key: str) -> int:
        attempt = self._attempts.get(key)
        if not attempt:
            return 0
        remaining = attempt.blocked_until - time.monotonic()
        return max(0, int(remaining + 0.999))

    def failure(self, key: str) -> None:
        attempt = self._attempts.setdefault(key, _Attempt())
        attempt.failures += 1
        delay = min(self._max_delay, 2 ** min(attempt.failures - 1, 5))
        attempt.blocked_until = time.monotonic() + delay

    def success(self, key: str) -> None:
        self._attempts.pop(key, None)
