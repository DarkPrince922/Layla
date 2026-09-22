"""Тесты прод-валидации конфигурации (спец. §7.5-§7.6)."""
from __future__ import annotations

from app.config import Settings


def test_dev_has_no_prod_problems():
    s = Settings(LAYLA_ENV="dev")
    assert s.validate_for_prod() == []


def test_prod_requires_secret_key():
    s = Settings(LAYLA_ENV="prod", LAYLA_SECRET_KEY="", LAYLA_JWT_SECRET="x" * 40)
    problems = s.validate_for_prod()
    assert any("LAYLA_SECRET_KEY" in p for p in problems)


def test_prod_rejects_default_jwt():
    s = Settings(LAYLA_ENV="prod", LAYLA_SECRET_KEY="k", LAYLA_JWT_SECRET="dev-insecure-jwt-secret")
    problems = s.validate_for_prod()
    assert any("JWT" in p for p in problems)


def test_prod_ok_with_real_secrets():
    s = Settings(LAYLA_ENV="prod", LAYLA_SECRET_KEY="realkey", LAYLA_JWT_SECRET="a" * 40)
    assert s.validate_for_prod() == []
