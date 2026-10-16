import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.chat import Message
from app.models.code_schedule import CodeSchedule
from app.models.job import Job
from app.services import code_scheduler, tool_chat
from tests.test_code_runtime import setup_code


@pytest.mark.parametrize("expression", ["* * * *", "60 * * * *", "*/0 * * * *", "0 24 * * *", "0 0 * 13 *", "0 0 * * 8", "0/5 * * * *"])
def test_invalid_cron(expression):
    with pytest.raises(ValueError):
        code_scheduler.cron_fields(expression)


def test_timezone_day_or_sunday_alias_and_dst():
    before = datetime(2026, 10, 16, 5, 59, tzinfo=UTC)
    assert code_scheduler.next_run("0 9 * * *", "Europe/Moscow", before) == before + timedelta(minutes=1)
    assert code_scheduler.next_run("0 0 1 * 7", "UTC", before).day == 18
    assert code_scheduler.next_run("30 2 * * *", "America/New_York", datetime(2026, 3, 8, 5, tzinfo=UTC)) == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)
    assert code_scheduler.next_run("30 1 * * *", "America/New_York", datetime(2026, 11, 1, 5, 30, tzinfo=UTC)) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


async def create(client, chat, **body):
    response = await client.post(f"/api/chats/{chat['id']}/schedules", json={
        "prompt": "Inspect the project", "at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(), **body,
    })
    assert response.status_code == 201, response.text
    return response.json()["schedule"]


async def test_crud_ownership_and_paused_schedules(client, db_sessionmaker):
    _, _, chat = await setup_code(client)
    row = await create(client, chat)
    endpoint = f"/api/chats/{chat['id']}/schedules"
    assert (await client.patch(endpoint + "/" + row["id"], json={"enabled": False})).status_code == 200
    called = []
    async def dispatch(*args):
        called.append(args)
    await code_scheduler.tick(db_sessionmaker, now=datetime.now(UTC) + timedelta(days=1), dispatch=dispatch)
    assert not called
    await client.post("/api/auth/register", json={"email": "schedules-other@example.com", "password": "hunter2hunter2"})
    assert (await client.get(endpoint)).status_code == 404
    assert (await client.delete(endpoint + "/" + row["id"])).status_code == 404


async def prepare_provider(client, monkeypatch):
    await client.post("/api/providers", json={"name": "Schedule mock", "kind": "openai_compatible",
        "base_url": "http://mock/v1", "default_model": "m", "active": True})
    async def stream(*args, **kwargs):
        yield "content", "Scheduled result"
    monkeypatch.setattr(tool_chat, "stream_turn", stream)


async def completed(maker, job_id):
    async with asyncio.timeout(5):
        while True:
            async with maker() as session:
                job = await session.get(Job, job_id)
                if job.status in ("done", "error", "cancelled"):
                    assert job.status == "done", job.error
                    break
            await asyncio.sleep(0.01)


async def test_two_pollers_admit_one_normal_code_job(client, db_sessionmaker, monkeypatch):
    _, _, chat = await setup_code(client)
    await prepare_provider(client, monkeypatch)
    schedule = await create(client, chat, mode="plan")
    now = datetime.fromisoformat(schedule["next_run"]) + timedelta(seconds=1)
    await asyncio.gather(code_scheduler.tick(db_sessionmaker, now=now), code_scheduler.tick(db_sessionmaker, now=now))
    async with db_sessionmaker() as session:
        row = await session.get(CodeSchedule, schedule["id"])
        assert not row.enabled and row.last_job_id and not row.last_error
        job = await session.get(Job, row.last_job_id)
        assert job.domain == "code" and job.result["mode"] == "plan"
        assert await session.scalar(select(func.count()).select_from(Job).where(Job.chat_id == chat["id"])) == 1
        job_id = row.last_job_id
    await completed(db_sessionmaker, job_id)


async def test_crash_after_admission_reuses_job_and_message(client, db_sessionmaker, monkeypatch):
    from app.api.chats import run_chat
    _, _, chat = await setup_code(client)
    await prepare_provider(client, monkeypatch)
    schedule = await create(client, chat)
    now = datetime.fromisoformat(schedule["next_run"]) + timedelta(seconds=1)
    async def crash(*args):
        await run_chat(*args)
        raise RuntimeError("crash before recording last_job_id")
    await code_scheduler.tick(db_sessionmaker, now=now, dispatch=crash)
    await code_scheduler.tick(db_sessionmaker, now=now + timedelta(seconds=31))
    async with db_sessionmaker() as session:
        row = await session.get(CodeSchedule, schedule["id"])
        assert row.last_job_id and not row.enabled
        assert await session.scalar(select(func.count()).select_from(Job).where(Job.chat_id == chat["id"])) == 1
        assert await session.scalar(select(func.count()).select_from(Message).where(
            Message.chat_id == chat["id"], Message.role == "user")) == 1
        job_id = row.last_job_id
    await completed(db_sessionmaker, job_id)


async def test_busy_chat_defers_and_recurring_misses_coalesce(client, db_sessionmaker, monkeypatch):
    _, _, chat = await setup_code(client)
    await prepare_provider(client, monkeypatch)
    schedule = await create(client, chat, at=None, cron="*/5 * * * *", timezone="Europe/Moscow", recurring=True)
    now = datetime.fromisoformat(schedule["next_run"]) + timedelta(days=10)
    async def busy(*_):
        raise HTTPException(409, "busy")
    await code_scheduler.tick(db_sessionmaker, now=now, dispatch=busy)
    async with db_sessionmaker() as session:
        row = await session.get(CodeSchedule, schedule["id"])
        assert row.enabled and row.last_error and not row.last_job_id
    await code_scheduler.tick(db_sessionmaker, now=now + timedelta(seconds=31))
    async with db_sessionmaker() as session:
        row = await session.get(CodeSchedule, schedule["id"])
        assert row.enabled and row.last_job_id and not row.last_error
        assert code_scheduler.aware(row.next_run) > now + timedelta(seconds=31)
        job_id = row.last_job_id
    await completed(db_sessionmaker, job_id)


async def test_project_switch_disables_old_schedule(client, db_sessionmaker):
    from app.models.chat import Chat
    _, _, chat = await setup_code(client)
    schedule = await create(client, chat)
    other = (await client.post("/api/projects", json={"name": "New project"})).json()
    async with db_sessionmaker() as session:
        row = await session.get(Chat, chat["id"])
        row.project_id = other["id"]
        await session.commit()
    await code_scheduler.tick(db_sessionmaker, now=datetime.fromisoformat(schedule["next_run"]) + timedelta(seconds=1))
    async with db_sessionmaker() as session:
        row = await session.get(CodeSchedule, schedule["id"])
        assert not row.enabled and row.last_error and not row.last_job_id


async def test_worktree_switch_never_redirects_a_scheduled_prompt(client, db_sessionmaker):
    from app.models.code_workspace import CodeWorkspace
    owner, project, chat = await setup_code(client)
    schedule = await create(client, chat)
    async with db_sessionmaker() as session:
        session.add(CodeWorkspace(owner_id=owner, chat_id=chat["id"], project_id=project["id"],
            name="Different copy", branch="copy", source_root=project["path"], active_slot=1, status="active"))
        await session.commit()
    await code_scheduler.tick(db_sessionmaker, now=datetime.fromisoformat(schedule["next_run"]) + timedelta(seconds=1))
    async with db_sessionmaker() as session:
        row = await session.get(CodeSchedule, schedule["id"])
        assert not row.enabled and "копия" in row.last_error and not row.last_job_id
