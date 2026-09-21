"""Secret-encryption invariants (spec §7.5)."""
from __future__ import annotations

from app.security import crypto


def test_encrypt_decrypt_roundtrip():
    secret = "sk-super-secret-api-key-1234567890"
    token = crypto.encrypt(secret)
    assert token != secret               # stored form is not plaintext
    assert crypto.decrypt(token) == secret


def test_ciphertext_is_nondeterministic():
    # Fernet embeds a random IV, so the same plaintext encrypts differently.
    assert crypto.encrypt("same") != crypto.encrypt("same")


def test_mask_hides_body():
    masked = crypto.mask("sk-abcdefgh1234")
    assert masked.endswith("1234")
    assert "abcdef" not in masked
    assert masked.startswith("•")


def test_mask_empty():
    assert crypto.mask("") == ""
