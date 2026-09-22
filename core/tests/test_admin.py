"""Admin bootstrap, closed registration, and admin user management (spec §4)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.user import AdminBootstrap, User
from app.services.auth import ensure_admin_bootstrapped


async def _promote(db_sessionmaker, email: str) -> None:
    async with db_sessionmaker() as s:
        u = await s.scalar(select(User).where(User.email == email))
        u.is_admin = True
        await s.commit()


@pytest.mark.asyncio
async def test_bootstrap_creates_admin_with_password(db_sessionmaker):
    async with db_sessionmaker() as s:
        await ensure_admin_bootstrapped(s)
    async with db_sessionmaker() as s:
        admins = (await s.scalars(select(User).where(User.is_admin.is_(True)))).all()
        boots = (await s.scalars(select(AdminBootstrap))).all()
    assert len(admins) == 1
    assert len(boots) == 1
    assert boots[0].email == admins[0].email
    assert len(boots[0].password) >= 12


@pytest.mark.asyncio
async def test_bootstrap_promotes_existing_user(db_sessionmaker, client):
    await client.post(
        "/api/auth/register",
        json={"email": "first@example.com", "password": "hunter2hunter2"},
    )
    async with db_sessionmaker() as s:
        await ensure_admin_bootstrapped(s)
    async with db_sessionmaker() as s:
        u = await s.scalar(select(User).where(User.email == "first@example.com"))
        boots = (await s.scalars(select(AdminBootstrap))).all()
    assert u.is_admin is True
    assert boots == []  # существующий пользователь — случайный пароль не создаётся


@pytest.mark.asyncio
async def test_bootstrap_endpoint_consumed_on_admin_login(db_sessionmaker, client):
    async with db_sessionmaker() as s:
        await ensure_admin_bootstrapped(s)
    body = (await client.get("/api/auth/bootstrap")).json()
    assert body["available"] and body["password"]
    r = await client.post(
        "/api/auth/login", json={"email": body["email"], "password": body["password"]}
    )
    assert r.status_code == 200
    assert (await client.get("/api/auth/bootstrap")).json()["available"] is False


@pytest.mark.asyncio
async def test_registration_closed_when_flag_off(client, monkeypatch):
    from app import config

    monkeypatch.setenv("LAYLA_ALLOW_OPEN_REGISTRATION", "0")
    config.get_settings.cache_clear()
    try:
        r = await client.post(
            "/api/auth/register",
            json={"email": "nope@example.com", "password": "hunter2hunter2"},
        )
        assert r.status_code == 403
    finally:
        monkeypatch.setenv("LAYLA_ALLOW_OPEN_REGISTRATION", "1")
        config.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_admin_creates_user_and_nonadmin_forbidden(db_sessionmaker, client):
    await client.post(
        "/api/auth/register",
        json={"email": "admin@example.com", "password": "hunter2hunter2"},
    )
    await _promote(db_sessionmaker, "admin@example.com")

    # Админ создаёт пользователя с автогенерацией пароля.
    r = await client.post("/api/admin/users", json={"email": "bob@example.com"})
    assert r.status_code == 201, r.text
    created = r.json()
    pw = created["generated_password"]
    assert pw and created["user"]["email"] == "bob@example.com"

    assert len((await client.get("/api/admin/users")).json()) == 2

    # Bob входит выданным паролем, но админом не является.
    await client.post("/api/auth/logout")
    r = await client.post("/api/auth/login", json={"email": "bob@example.com", "password": pw})
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True
    assert (await client.get("/api/admin/users")).status_code == 403


@pytest.mark.asyncio
async def test_cannot_demote_last_admin(db_sessionmaker, client):
    await client.post(
        "/api/auth/register",
        json={"email": "solo@example.com", "password": "hunter2hunter2"},
    )
    await _promote(db_sessionmaker, "solo@example.com")
    admin_id = (await client.get("/api/auth/me")).json()["id"]
    r = await client.patch(f"/api/admin/users/{admin_id}", json={"is_admin": False})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_change_password_flow(client):
    await client.post(
        "/api/auth/register",
        json={"email": "cp@example.com", "password": "hunter2hunter2"},
    )
    # неверный текущий пароль
    r = await client.post(
        "/api/auth/change-password",
        json={"current_password": "wrongwrong", "new_password": "brandnew12345"},
    )
    assert r.status_code == 400
    # корректная смена
    r = await client.post(
        "/api/auth/change-password",
        json={"current_password": "hunter2hunter2", "new_password": "brandnew12345"},
    )
    assert r.status_code == 200
    await client.post("/api/auth/logout")
    r = await client.post(
        "/api/auth/login", json={"email": "cp@example.com", "password": "brandnew12345"}
    )
    assert r.status_code == 200
