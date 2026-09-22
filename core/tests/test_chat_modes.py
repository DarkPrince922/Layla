"""Режимы чата (авто / с подтверждением / план), зависшие задачи и удаление истории."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.job import Job
from app.services import jobs, project_agent

pytestmark = pytest.mark.asyncio


async def _setup(client, email="modes@example.com"):
    await client.post("/api/auth/register", json={"email": email, "password": "hunter2hunter2"})
    await client.post("/api/providers", json={"name": "M", "kind": "openai_compatible",
                                              "base_url": "http://p/v1", "default_model": "m", "active": True})


def _script(monkeypatch, turns: list[list[dict]], seen: list | None = None):
    """Подменить модель: каждый ход — список вызовов инструментов; пустой — финальный текст."""
    state = {"turn": 0}

    async def stream_turn(provider, key, model, conversation, available):
        if seen is not None:
            seen.append({"tools": {t["function"]["name"] for t in available}, "conversation": list(conversation)})
        calls = turns[state["turn"]] if state["turn"] < len(turns) else []
        state["turn"] += 1
        if calls:
            yield ("tool_calls", [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                for i, c in enumerate(calls)
            ])
        else:
            yield ("content", "Готово")

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream_turn)


def _write(path, content="x"):
    return {"name": "write_file", "args": {"path": path, "content": content, "expected_sha256": None}}


async def _wait(client, job_id, predicate, tries=500):
    body = {}
    for _ in range(tries):
        await asyncio.sleep(0.02)
        body = (await client.get(f"/api/jobs/{job_id}")).json()
        if predicate(body):
            return body
    return body


async def _start(client, mode, domain="design"):
    chat = (await client.post("/api/chats", json={"domain": domain, "model": "m"})).json()
    r = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "сделай", "mode": mode})
    assert r.status_code == 202, r.text
    return chat, r.json()


async def test_plan_mode_offers_only_read_tools(client, monkeypatch):
    await _setup(client)
    seen: list = []
    _script(monkeypatch, [[_write("a.html")]], seen)
    chat, job = await _start(client, "plan")
    done = await _wait(client, job["id"], lambda b: b["status"] in ("done", "error"))
    assert done["status"] == "done", done
    assert seen[0]["tools"] == {"list_files", "read_file"}
    assert "PLAN MODE" in seen[0]["conversation"][0]["content"]
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    root = Path(next(p for p in (await client.get("/api/projects")).json() if p["id"] == detail["project_id"])["path"])
    assert not (root / "a.html").exists()  # план ничего не меняет
    assert detail["messages"][-1]["meta"]["mode"] == "plan"


async def test_confirm_mode_waits_and_applies_on_approve(client, monkeypatch):
    await _setup(client)
    _script(monkeypatch, [[_write("ok.html", "<h1>ok</h1>")]])
    chat, job = await _start(client, "confirm")
    waiting = await _wait(client, job["id"], lambda b: (b["result"] or {}).get("approval"))
    approval = waiting["result"]["approval"]
    assert waiting["status"] == "running" and approval["path"] == "ok.html"
    # Пока ждёт — превью изменения уже в сообщении, но файла ещё нет.
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    pending = detail["messages"][-1]["meta"]["tools"][-1]
    assert pending["status"] == "pending" and "+<h1>ok</h1>" in pending["change"]["diff"]
    root = Path(next(p for p in (await client.get("/api/projects")).json() if p["id"] == detail["project_id"])["path"])
    assert not (root / "ok.html").exists()

    r = await client.post(f"/api/jobs/{job['id']}/decision", json={"approval_id": approval["id"], "decision": "approve"})
    assert r.status_code == 200, r.text
    done = await _wait(client, job["id"], lambda b: b["status"] in ("done", "error"))
    assert done["status"] == "done", done
    assert (root / "ok.html").read_text() == "<h1>ok</h1>"
    assert (await client.post(f"/api/jobs/{job['id']}/decision",
                              json={"approval_id": approval["id"], "decision": "approve"})).status_code == 409


async def test_confirm_mode_reject_keeps_file_untouched(client, monkeypatch):
    await _setup(client)
    seen: list = []
    _script(monkeypatch, [[_write("no.html")]], seen)
    chat, job = await _start(client, "confirm")
    waiting = await _wait(client, job["id"], lambda b: (b["result"] or {}).get("approval"))
    await client.post(f"/api/jobs/{job['id']}/decision",
                      json={"approval_id": waiting["result"]["approval"]["id"], "decision": "reject"})
    await _wait(client, job["id"], lambda b: b["status"] in ("done", "error"))
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert detail["messages"][-1]["meta"]["tools"][-1]["status"] == "rejected"
    # Модель узнаёт об отказе из результата инструмента.
    assert "отклонил" in seen[-1]["conversation"][-1]["content"]


async def test_approve_all_stops_asking(client, monkeypatch):
    await _setup(client)
    _script(monkeypatch, [[_write("one.html"), _write("two.html")]])
    chat, job = await _start(client, "confirm")
    waiting = await _wait(client, job["id"], lambda b: (b["result"] or {}).get("approval"))
    await client.post(f"/api/jobs/{job['id']}/decision",
                      json={"approval_id": waiting["result"]["approval"]["id"], "decision": "approve_all"})
    done = await _wait(client, job["id"], lambda b: b["status"] in ("done", "error"))
    assert done["status"] == "done", done
    tools = (await client.get(f"/api/chats/{chat['id']}")).json()["messages"][-1]["meta"]["tools"]
    assert [t["status"] for t in tools] == ["done", "done"]


async def test_auto_mode_has_no_hard_round_error(client, monkeypatch):
    """Упор в страховочный лимит — мягкая остановка, а не ошибка задачи."""
    await _setup(client)
    monkeypatch.setattr(project_agent, "MAX_ROUNDS", 1)
    _script(monkeypatch, [[_write("a.html")], [_write("b.html")]])
    chat, job = await _start(client, "auto")
    done = await _wait(client, job["id"], lambda b: b["status"] in ("done", "error"))
    assert done["status"] == "done", done
    last = (await client.get(f"/api/chats/{chat['id']}")).json()["messages"][-1]
    assert "продолжай" in last["content"] and not last["meta"].get("error")


async def test_stuck_job_does_not_lock_chat(client, db_sessionmaker, monkeypatch):
    """Задача «работает» в БД, но воркера нет — чат не должен быть заперт навсегда."""
    await _setup(client)
    _script(monkeypatch, [])
    chat = (await client.post("/api/chats", json={"domain": "design", "model": "m"})).json()
    me = (await client.get("/api/auth/me")).json()["id"]
    old = datetime.now(UTC) - timedelta(minutes=5)
    async with db_sessionmaker() as s:
        ghost = Job(owner_id=me, domain="design", kind="project.agent", title="ghost", chat_id=chat["id"],
                    status="running", created_at=old, updated_at=old)
        s.add(ghost)
        await s.commit()
        ghost_id = ghost.id
    r = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "снова"})
    assert r.status_code == 202, r.text
    assert (await client.get(f"/api/jobs/{ghost_id}")).json()["status"] == "error"


async def test_fresh_job_still_blocks_second_turn(client, db_sessionmaker):
    """Защита от двойной отправки остаётся: свежая активная задача блокирует чат."""
    await _setup(client)
    chat = (await client.post("/api/chats", json={"domain": "design", "model": "m"})).json()
    me = (await client.get("/api/auth/me")).json()["id"]
    async with db_sessionmaker() as s:
        s.add(Job(owner_id=me, domain="design", kind="project.agent", title="live", chat_id=chat["id"],
                  status="running", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)))
        await s.commit()
    assert (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "x"})).status_code == 409


async def test_stop_releases_dead_job(client, db_sessionmaker):
    await _setup(client)
    chat = (await client.post("/api/chats", json={"domain": "osint", "model": "m"})).json()
    me = (await client.get("/api/auth/me")).json()["id"]
    async with db_sessionmaker() as s:
        ghost = Job(owner_id=me, domain="osint", kind="project.agent", title="ghost", chat_id=chat["id"],
                    status="running", created_at=datetime.now(UTC))
        s.add(ghost)
        await s.commit()
        ghost_id = ghost.id
    r = await client.post(f"/api/jobs/{ghost_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert ghost_id not in jobs._tasks


async def test_deleting_chat_removes_its_workspace(client, monkeypatch):
    """Чат Дизайна/OSINT удаляется вместе со своей папкой — проекты не копятся в «Коде»."""
    await _setup(client)
    _script(monkeypatch, [[_write("x.html")]])
    chat, job = await _start(client, "auto")
    await _wait(client, job["id"], lambda b: b["status"] in ("done", "error"))
    project_id = (await client.get(f"/api/chats/{chat['id']}")).json()["project_id"]
    root = Path(next(p for p in (await client.get("/api/projects")).json() if p["id"] == project_id)["path"])
    assert root.exists()
    assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 204
    assert all(p["id"] != project_id for p in (await client.get("/api/projects")).json())
    assert not root.exists()


async def test_code_chat_delete_keeps_project(client):
    await _setup(client)
    project = (await client.post("/api/projects", json={"name": "Сайт"})).json()
    chat = (await client.post("/api/chats", json={"domain": "code", "project_id": project["id"]})).json()
    assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 204
    assert any(p["id"] == project["id"] for p in (await client.get("/api/projects")).json())


async def test_clear_history_skips_running_chat(client, db_sessionmaker):
    await _setup(client)
    ids = [(await client.post("/api/chats", json={"domain": "osint", "model": "m"})).json()["id"] for _ in range(3)]
    other = (await client.post("/api/chats", json={"domain": "design", "model": "m"})).json()["id"]
    me = (await client.get("/api/auth/me")).json()["id"]
    async with db_sessionmaker() as s:
        s.add(Job(owner_id=me, domain="osint", kind="project.agent", title="live", chat_id=ids[0],
                  status="running", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)))
        await s.commit()
    r = await client.delete("/api/chats", params={"domain": "osint"})
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 2, "skipped": 1}
    left = {c["id"] for c in (await client.get("/api/chats")).json()}
    assert left == {ids[0], other}  # другой раздел не тронут


async def test_delete_project_removes_files_and_chats(client):
    await _setup(client)
    project = (await client.post("/api/projects", json={"name": "Удаляемый"})).json()
    root = Path(next(p for p in (await client.get("/api/projects")).json() if p["id"] == project["id"])["path"])
    await client.put(f"/api/projects/{project['id']}/file",
                     json={"path": "index.html", "content": "hi", "expected_sha256": None})
    chat = (await client.post("/api/chats", json={"domain": "code", "project_id": project["id"]})).json()
    assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 204
    assert not root.exists()
    assert (await client.get(f"/api/chats/{chat['id']}")).status_code == 404
