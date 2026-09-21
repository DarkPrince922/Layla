"""Argon2 password hashing (spec §4)."""
from __future__ import annotations

from app.security import passwords


def test_hash_and_verify():
    h = passwords.hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert passwords.verify_password("correct horse battery staple", h)
    assert not passwords.verify_password("wrong", h)


def test_verify_rejects_garbage_hash():
    assert not passwords.verify_password("x", "not-a-valid-hash")
