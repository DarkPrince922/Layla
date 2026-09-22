"""Test fixtures: in-memory async SQLite DB + ASGI client with dependency
overrides. No external services (Postgres/LiteLLM) are touched.
"""
from __future__ import annotations

import os

# Configure a valid Fernet key and a throwaway DB URL before importing the app.
os.environ.setdefault("LAYLA_SECRET_KEY", "test-secret-key-for-derivation")
os.environ.setdefault("LAYLA_JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("LAYLA_ENV", "dev")
# Тесты создают пользователей через открытую регистрацию (в проде она закрыта).
os.environ.setdefault("LAYLA_ALLOW_OPEN_REGISTRATION", "1")
import tempfile  # noqa: E402
os.environ.setdefault("LAYLA_PROJECTS_DIR", tempfile.mkdtemp(prefix="layla-proj-"))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.db import Base, get_session, get_sessionmaker  # noqa: E402
import app.models  # noqa: E402,F401  (register tables)
from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def db_sessionmaker():
    # StaticPool keeps a single in-memory DB across connections.
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_sessionmaker):
    async def _override_get_session():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_sessionmaker] = lambda: db_sessionmaker
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
