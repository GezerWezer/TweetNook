import hashlib

import pytest

from tweetnook.core import passwords
from tweetnook.core.passwords import (
    RecentPasswordCheck,
    hash_password,
    password_needs_rehash,
    verify_password,
)


def test_scrypt_uses_independent_salts_and_verifies_unicode():
    first, second = hash_password("☕ coffee"), hash_password("☕ coffee")
    assert first != second
    assert verify_password("☕ coffee", first)
    assert not verify_password("wrong", first)
    assert verify_password("old", hashlib.sha256(b"old").hexdigest())


@pytest.mark.parametrize(
    "stored", ["bad", "a" * 64, "scrypt$999999999$8$1$00$00", "scrypt$16384$8$1$zz$zz"]
)
def test_malformed_password_hash_is_rejected(stored):
    assert not verify_password("password", stored)


def test_hash_with_other_saved_parameters_still_verifies_and_asks_for_rehash():
    salt = bytes(range(16))
    # 32 MiB exceeds hashlib's default limit, so a higher saved cost needs its own allowance.
    digest = hashlib.scrypt(b"secret", salt=salt, n=32768, r=8, p=1, maxmem=64 << 20, dklen=32)
    stored = f"scrypt$32768$8$1${salt.hex()}${digest.hex()}"
    assert verify_password("secret", stored)
    assert not verify_password("wrong", stored)
    assert password_needs_rehash(stored)
    assert not password_needs_rehash(hash_password("secret"))
    assert password_needs_rehash(hashlib.sha256(b"secret").hexdigest())


@pytest.mark.parametrize(
    "parameters", ["1073741824$8$1", "16385$8$1", "16384$0$1", "16384$8$0", "16384$8000$1"]
)
def test_unusable_saved_parameters_are_rejected_before_any_allocation(parameters, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("scrypt must not run")

    monkeypatch.setattr(passwords.hashlib, "scrypt", forbidden)
    assert not verify_password("secret", f"scrypt${parameters}${'00' * 16}${'00' * 32}")


def test_recent_check_skips_scrypt_only_for_the_same_correct_password_and_hash(monkeypatch):
    stored = hash_password("secret")
    calls = []
    original = passwords.verify_password

    def counted(password, saved):
        calls.append(password)
        return original(password, saved)

    monkeypatch.setattr(passwords, "verify_password", counted)
    clock = [100.0]
    monkeypatch.setattr(passwords.time, "monotonic", lambda: clock[0])
    check = RecentPasswordCheck(ttl_seconds=60)

    assert check.verify("secret", stored)
    assert check.verify("secret", stored)
    assert calls == ["secret"]

    assert not check.verify("wrong", stored)
    assert not check.verify("wrong", stored)
    assert calls == ["secret", "wrong", "wrong"]

    # A changed password must not be satisfied by the old success.
    assert not check.verify("secret", hash_password("changed"))

    calls.clear()
    clock[0] = 161.0
    assert check.verify("secret", stored)
    assert calls == ["secret"]
