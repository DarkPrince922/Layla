"""Тесты сборки настроек (DATABASE_URL из частей, кодирование пароля)."""
from __future__ import annotations

from app.config import Settings


def test_database_url_assembled_from_parts(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "layla")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "layla")
    s = Settings()
    assert s.database_url == "postgresql+asyncpg://layla:secret@postgres:5432/layla"


def test_database_url_password_is_url_encoded(monkeypatch):
    """Спецсимволы в пароле не должны ломать строку подключения."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/w:rd")
    s = Settings()
    assert "p%40ss%2Fw%3Ard" in s.database_url
    assert "@postgres" not in s.database_url.split("p%40ss")[0]  # хост не спутан


def test_explicit_database_url_wins(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("POSTGRES_PASSWORD", "ignored")
    s = Settings()
    assert s.database_url == "sqlite+aiosqlite:///:memory:"
