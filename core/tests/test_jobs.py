"""Фоновые задачи: раннер, изоляция, панель, фоновая генерация Design."""
from __future__ import annotations

import asyncio

import pytest

import app.services.provider_client as pc
from app.services import jobs


async def _wait_job(client, job_id, tries=150):
    for _ in range(tries):
        await asyncio.sleep(0.02)
        r = await client.get(f"/api/jobs/{job_id}")
        body = r.json()
        if body["status"] in ("done", "error"):
            return body
    return body


async def _register(client, email="j@example.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "hunter2hunter2"})
    return (await client.get("/api/auth/me")).json()["id"]


@pytest.mark.asyncio
async def test_generic_job_runner(db_sessionmaker, client):
    uid = await _register(client)
    async with db_sessionmaker() as s:
        job = await jobs.create_job(s, owner_id=uid, domain="design", kind="test", title="T")
        job_id = job.id

    async def worker(h: jobs.JobHandle) -> None:
        await h.step("шаг 1", progress=0.5)
        await h.reason("думаю…", flush=True)
        await h.step("шаг 2", progress=0.8)
        await h.set_result({"ok": True})

    jobs.launch(db_sessionmaker, job_id, worker)
    body = await _wait_job(client, job_id)
    assert body["status"] == "done"
    assert body["progress"] == 1.0
    assert [st["text"] for st in body["steps"]] == ["шаг 1", "шаг 2"]
    assert "думаю" in body["reasoning"]
    assert body["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_failing_worker_marks_error(db_sessionmaker, client):
    uid = await _register(client)
    async with db_sessionmaker() as s:
        job = await jobs.create_job(s, owner_id=uid, domain="code", kind="test", title="X")
        job_id = job.id

    async def worker(h: jobs.JobHandle) -> None:
        await h.step("начал")
        raise RuntimeError("бум")

    jobs.launch(db_sessionmaker, job_id, worker)
    body = await _wait_job(client, job_id)
    assert body["status"] == "error"
    assert "бум" in (body["error"] or "")


@pytest.mark.asyncio
async def test_job_isolation_and_active_filter(db_sessionmaker, client):
    uid = await _register(client, "owner@example.com")
    async with db_sessionmaker() as s:
        job = await jobs.create_job(s, owner_id=uid, domain="design", kind="test", title="mine")
        job_id = job.id

    # активный фильтр показывает queued
    active = (await client.get("/api/jobs?active=true")).json()
    assert any(j["id"] == job_id for j in active)

    # другой пользователь не видит чужую задачу
    await client.post("/api/auth/logout")
    await _register(client, "other@example.com")
    assert (await client.get(f"/api/jobs/{job_id}")).status_code == 404


@pytest.mark.asyncio
async def test_dismiss_only_finished(db_sessionmaker, client):
    uid = await _register(client)
    async with db_sessionmaker() as s:
        active = await jobs.create_job(s, owner_id=uid, domain="code", kind="t", title="a")
        active_id = active.id
        done = await jobs.create_job(s, owner_id=uid, domain="code", kind="t", title="b")
        done.status = "done"
        await s.commit()
        done_id = done.id

    assert (await client.delete(f"/api/jobs/{active_id}")).status_code == 409
    assert (await client.delete(f"/api/jobs/{done_id}")).status_code == 204


@pytest.mark.asyncio
async def test_design_generate_runs_in_background(client, monkeypatch):
    await client.post(
        "/api/auth/register",
        json={"email": "bg@example.com", "password": "hunter2hunter2"},
    )
    await client.post(
        "/api/providers",
        json={
            "name": "M",
            "kind": "openai_compatible",
            "base_url": "http://prov.local/v1",
            "default_model": "gpt-4o",
            "active": True,
        },
    )

    async def fake_stream(provider, key, model, messages):
        yield ("reasoning", "продумываю секции")
        yield ("content", "```html\n<h1>BG</h1>\n```")

    monkeypatch.setattr(pc, "stream_chat", fake_stream)

    r = await client.post(
        "/api/designs/generate",
        json={"stack": "html", "brief": {"artifact_type": "Landing"}},
    )
    assert r.status_code == 202, r.text
    body = await _wait_job(client, r.json()["id"])
    assert body["status"] == "done", body
    assert body["result"].get("design_id")
    assert "продумываю" in body["reasoning"]

    designs = (await client.get("/api/designs")).json()
    assert any("<h1>BG</h1>" in d["files"][0]["content"] for d in designs)


@pytest.mark.asyncio
async def test_triage_runs_in_background(client, monkeypatch):
    await client.post(
        "/api/auth/register", json={"email": "tr@example.com", "password": "hunter2hunter2"}
    )
    await client.post(
        "/api/providers",
        json={"name": "M", "kind": "openai_compatible", "base_url": "http://p/v1",
              "default_model": "gpt-4o", "active": True},
    )
    e = (await client.post("/api/engagements", json={"target": "example.com"})).json()
    f = (await client.post(
        f"/api/engagements/{e['id']}/findings",
        json={"severity": "HIGH", "type": "SQLi", "url": "https://example.com/a"},
    )).json()

    async def fake_stream(provider, key, model, messages):
        yield ("reasoning", "оцениваю находку")
        yield ("content", "Похоже на истинное срабатывание.")

    monkeypatch.setattr(pc, "stream_chat", fake_stream)
    r = await client.post(f"/api/engagements/{e['id']}/findings/{f['id']}/triage/bg", json={})
    assert r.status_code == 202, r.text
    body = await _wait_job(client, r.json()["id"])
    assert body["status"] == "done", body
    assert "истинное" in body["result"].get("verdict", "")
    assert "оцениваю" in body["reasoning"]


@pytest.mark.asyncio
async def test_coding_agent_runs_in_background(client, tmp_path, monkeypatch):
    from app.config import get_settings
    from app.services import project_agent

    monkeypatch.setattr(get_settings(), "projects_dir", str(tmp_path))
    await client.post(
        "/api/auth/register", json={"email": "ca@example.com", "password": "hunter2hunter2"}
    )
    await client.post(
        "/api/providers",
        json={"name": "M", "kind": "openai_compatible", "base_url": "http://p/v1",
              "default_model": "gpt-4o", "active": True},
    )
    project = (await client.post("/api/projects", json={"name": "Build"})).json()
    chat = (await client.post(
        "/api/chats", json={"project_id": project["id"], "model": "gpt-4o"}
    )).json()

    async def fake_run(provider, key, model, payload, root, permissions):
        yield {"reasoning": "планирую структуру"}
        yield {"tool": {"id": "t1", "name": "write_file", "path": "index.html",
                        "status": "done",
                        "change": {"operation": "create", "path": "index.html",
                                   "diff": "+<h1>hi</h1>", "before_sha256": None,
                                   "after_sha256": "x"}}}
        yield {"delta": "Готово. Файл создан."}

    monkeypatch.setattr(project_agent, "run", fake_run)
    r = await client.post(
        f"/api/chats/{chat['id']}/agent-run",
        json={"content": "Создай простой сайт", "model": "gpt-4o"},
    )
    assert r.status_code == 202, r.text
    body = await _wait_job(client, r.json()["id"])
    assert body["status"] == "done", body
    assert body["result"].get("chat_id") == chat["id"]
    assert "планирую" in body["reasoning"]
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert any(m["role"] == "assistant" and "Готово" in m["content"] for m in detail["messages"])
