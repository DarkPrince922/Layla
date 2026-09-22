"""Password hashing with Argon2id (spec §4)."""
from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_ph = PasswordHasher()

# Без похожих символов (0/O, 1/l/I), чтобы пароль было легко переписать вручную.
_ALPHABET = "".join(
    c for c in (string.ascii_letters + string.digits) if c not in "0O1lI"
)


def generate_password(length: int = 20) -> str:
    """Сгенерировать криптостойкий пароль (для бутстрапа админа и сброса)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(max(12, length)))


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _ph.check_needs_rehash(hashed)
    except InvalidHashError:
        return True
