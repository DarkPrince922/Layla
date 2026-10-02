"""Роли: свои роли, правка промта, сброс встроенных, неизменность прав встроенных."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _login(client, email="roles@example.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "hunter2hunter2"})


async def test_create_edit_delete_custom_role(client):
    await _login(client)
    role = (await client.post("/api/personas", json={
        "name": "Копирайтер", "instructions": "Пиши коротко.", "allowed_tools": ["files.read", "venue.exec"],
        "default_mode": "plan", "default_model": "gpt-4o",
    })).json()
    assert role["kind"] == "custom" and not role["is_builtin"]
    assert role["allowed_tools"] == ["files.read"]  # лишние права отброшены
    assert role["default_mode"] == "plan" and role["default_model"] == "gpt-4o"
    assert role["hitl_required"] is True

    edited = (await client.patch(f"/api/personas/{role['id']}", json={
        "instructions": "Пиши длинно.", "allowed_tools": ["files.read", "files.write"], "default_mode": None,
    })).json()
    assert edited["instructions"] == "Пиши длинно."
    assert edited["allowed_tools"] == ["files.read", "files.write"]
    assert edited["default_mode"] is None and edited["default_model"] == "gpt-4o"  # не переданное не трогаем

    assert (await client.delete(f"/api/personas/{role['id']}")).status_code == 204
    assert all(p["id"] != role["id"] for p in (await client.get("/api/personas")).json())


async def test_builtin_prompt_editable_but_rights_fixed_and_resettable(client):
    await _login(client)
    pentest = next(p for p in (await client.get("/api/personas")).json() if p["kind"] == "pentest")
    original = dict(pentest)
    edited = (await client.patch(f"/api/personas/{pentest['id']}", json={
        "name": "Мой пентест", "instructions": "Свой промт", "allowed_tools": ["files.read"],
    })).json()
    assert edited["name"] == "Мой пентест" and edited["instructions"] == "Свой промт"
    assert edited["allowed_tools"] == original["allowed_tools"]  # права встроенной роли не меняются
    assert edited["hitl_required"] is True
    assert (await client.delete(f"/api/personas/{pentest['id']}")).status_code == 400

    reset = (await client.post(f"/api/personas/{pentest['id']}/reset")).json()
    assert reset["name"] == original["name"] and reset["instructions"] == original["instructions"]


async def test_custom_role_cannot_be_reset_and_roles_are_private(client):
    await _login(client)
    role = (await client.post("/api/personas", json={"name": "Моя"})).json()
    assert (await client.post(f"/api/personas/{role['id']}/reset")).status_code == 400
    await client.post("/api/auth/logout")
    await _login(client, "other-roles@example.com")
    assert (await client.patch(f"/api/personas/{role['id']}", json={"name": "чужая"})).status_code == 404
    assert (await client.delete(f"/api/personas/{role['id']}")).status_code == 404


async def test_custom_role_prompt_reaches_the_model(client, monkeypatch):
    import asyncio

    from app.services import project_agent

    await _login(client)
    await client.post("/api/providers", json={"name": "M", "kind": "openai_compatible",
                                              "base_url": "http://p/v1", "default_model": "m", "active": True})
    role = (await client.post("/api/personas", json={"name": "Пират", "instructions": "Отвечай как пират."})).json()
    seen = {}

    async def fake_run(provider, key, model, payload, root, permissions, **kw):
        seen["system"] = payload[0]
        seen["permissions"] = permissions
        yield {"delta": "Йо-хо-хо"}

    monkeypatch.setattr(project_agent, "run", fake_run)
    chat = (await client.post("/api/chats", json={"domain": "code", "model": "m", "persona_id": role["id"]})).json()
    job = (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "привет"})).json()
    for _ in range(300):
        await asyncio.sleep(0.02)
        if (await client.get(f"/api/jobs/{job['id']}")).json()["status"] not in ("queued", "running"):
            break
    assert seen["system"] == {"role": "system", "content": "Отвечай как пират."}
    # Новая роль по умолчанию: файлы, запуск кода в песочнице и Git.
    assert seen["permissions"] == ["files.read", "files.write", "code.run", "repo.git"]
