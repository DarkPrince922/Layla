"""End-to-end auth + secret-masking flow (spec §4, §5.1, §7.5)."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_register_login_me_and_persona_seed(client):
    # Register -> sets session cookie, seeds built-in personas.
    r = await client.post(
        "/api/auth/register",
        json={"email": "op@example.com", "password": "hunter2hunter2", "display_name": "Op"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "op@example.com"

    # /me works with the cookie the client stored.
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "op@example.com"

    # Built-in personas were seeded (Пентест/OSINT/Разработка...).
    r = await client.get("/api/personas")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert {"Разработка", "Пентест", "OSINT", "Безопасность"} <= names


@pytest.mark.asyncio
async def test_duplicate_email_rejected(client):
    body = {"email": "dup@example.com", "password": "hunter2hunter2"}
    r1 = await client.post("/api/auth/register", json=body)
    assert r1.status_code == 201
    r2 = await client.post("/api/auth/register", json=body)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_bad_login_rejected(client):
    await client.post(
        "/api/auth/register",
        json={"email": "u@example.com", "password": "hunter2hunter2"},
    )
    r = await client.post(
        "/api/auth/login", json={"email": "u@example.com", "password": "wrongpassword"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_provider_api_key_never_returned(client):
    await client.post(
        "/api/auth/register",
        json={"email": "p@example.com", "password": "hunter2hunter2"},
    )
    secret = "sk-THIS-MUST-NOT-LEAK-0987654321"
    r = await client.post(
        "/api/providers",
        json={"name": "OpenAI", "kind": "anthropic", "api_key": secret},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # The plaintext secret must never appear in any response field.
    assert secret not in r.text
    assert body["has_secret"] is True
    assert "api_key" not in body
    assert "secret_ref" not in body

    r = await client.get("/api/providers")
    assert secret not in r.text
