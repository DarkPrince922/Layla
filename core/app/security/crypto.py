"""Application-level encryption for secrets at rest (spec §7.5).

Every provider API key, intelligence-API key and SSH key is stored encrypted
with a Fernet (AES-128-CBC + HMAC) token derived from LAYLA_SECRET_KEY. Plain
secrets never touch the main tables — models hold an opaque ``secret_ref``.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _load_key() -> bytes:
    """Return a valid Fernet key.

    Accepts a proper urlsafe-base64 32-byte key directly. If the configured
    value is not a valid Fernet key (e.g. a dev placeholder), derive a stable
    32-byte key from it via SHA-256 so local development still works. In prod a
    real generated key must be supplied.
    """
    settings = get_settings()
    raw = settings.secret_key or "layla-dev-only-insecure-key"
    try:
        # Validate: a real Fernet key is 32 url-safe base64 bytes.
        Fernet(raw.encode())
        return raw.encode()
    except (ValueError, TypeError):
        digest = hashlib.sha256(raw.encode()).digest()
        return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_load_key())


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 secret, returning a urlsafe token string."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt`."""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - defensive
        raise ValueError("Could not decrypt secret (wrong key or corrupt data)") from exc


def mask(plaintext: str, visible: int = 4) -> str:
    """Return a UI-safe masked form of a secret, e.g. ``sk-…a1b2``."""
    if not plaintext:
        return ""
    if len(plaintext) <= visible:
        return "•" * len(plaintext)
    return "•" * 4 + plaintext[-visible:]
