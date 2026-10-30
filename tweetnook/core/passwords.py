"""Versioned salted password digests, including released SHA-256 verification."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
# Hashes carry their own cost so raising it later keeps saved passwords valid. The ceiling stops
# a damaged or hand-edited value from allocating unbounded memory during a login.
_SCRYPT_MAX_BYTES = 1024 * 1024 * 1024
RECENT_CHECK_SECONDS = 60.0
_LEGACY_HASH = re.compile(r"[0-9a-f]{64}")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def is_legacy_password_hash(value: str) -> bool:
    return _LEGACY_HASH.fullmatch(value) is not None


def _scrypt_fields(stored: str) -> tuple[int, int, int, bytes, bytes] | None:
    try:
        kind, n_text, r_text, p_text, salt_hex, digest_hex = stored.split("$")
        n, r, p = int(n_text), int(r_text), int(p_text)
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    except ValueError:
        return None
    if kind != "scrypt" or n < 2 or n & (n - 1) or r < 1 or p < 1:
        return None
    if _scrypt_memory(n, r, p) > _SCRYPT_MAX_BYTES or len(salt) != 16 or len(expected) != 32:
        return None
    return n, r, p, salt, expected


def _scrypt_memory(n: int, r: int, p: int) -> int:
    return 128 * r * (n + p + 2)


def password_needs_rehash(stored: str) -> bool:
    fields = _scrypt_fields(stored)
    return fields is None or fields[:3] != (SCRYPT_N, SCRYPT_R, SCRYPT_P)


def verify_password(password: str, stored: str) -> bool:
    if is_legacy_password_hash(stored):
        return hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).hexdigest(), stored)
    fields = _scrypt_fields(stored)
    if fields is None:
        return False
    n, r, p, salt, expected = fields
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=_scrypt_memory(n, r, p),
            dklen=32,
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


class RecentPasswordCheck:
    """Remember the latest success so a Basic-auth client doesn't pay for scrypt per request.

    Only a digest under a per-process key is kept, and failures are never remembered.
    """

    def __init__(self, ttl_seconds: float = RECENT_CHECK_SECONDS):
        self._key = secrets.token_bytes(32)
        self._ttl_seconds = ttl_seconds
        self._latest: tuple[bytes, float] | None = None

    def verify(self, password: str, stored: str) -> bool:
        token = hmac.digest(
            self._key,
            f"{stored}\0{password}".encode("utf-8", "surrogatepass"),
            "sha256",
        )
        latest = self._latest
        if latest is not None and hmac.compare_digest(latest[0], token):
            if time.monotonic() < latest[1]:
                return True
        if not verify_password(password, stored):
            return False
        self._latest = (token, time.monotonic() + self._ttl_seconds)
        return True
