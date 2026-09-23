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
# Запуск кода в тестах — только через подменённые клиенты, без настоящих сервисов.
os.environ.setdefault("LAYLA_SANDBOX_URL", "")
os.environ.setdefault("LAYLA_PISTON_URL", "")
import tempfile  # noqa: E402
os.environ.setdefault("LAYLA_PROJECTS_DIR", tempfile.mkdtemp(prefix="layla-proj-"))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.db import Base, get_session, get_sessionmaker  # noqa: E402
import app.models  # noqa: E402,F401  (register tables)
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _instant_retries(monkeypatch):
    """Автоповтор к провайдеру в тестах — без реальных пауз 1–16 с."""
    from app.services import provider_errors

    monkeypatch.setattr(provider_errors, "RETRY_DELAYS", (0, 0, 0, 0, 0))


@pytest_asyncio.fixture
async def db_sessionmaker(tmp_path_factory):
    # Файловая SQLite (а не :memory:+StaticPool): у каждой параллельной фоновой
    # задачи своё соединение, поэтому они не сериализуются на одном подключении.
    # WAL + busy timeout: конкурентные записи ждут, а не падают с «database locked».
    db_file = tmp_path_factory.mktemp("layladb") / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"timeout": 30, "check_same_thread": False},
    )
    # Проверка внешних ключей, как в Postgres: без неё SQLite молча пропускал
    # ссылки на ещё не записанные строки, и такие баги всплывали только на сервере.
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=30000")
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    # Снять фоновые задачи до закрытия движка — иначе незавершённые воркеры
    # текут в следующий тест и создают гонки.
    from app.services import jobs

    await jobs.shutdown()
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
