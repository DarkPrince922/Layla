"""Bot transport, private pairing, owner isolation, menus and durable delivery."""
import asyncio
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.models.job import Job
from app.models.telegram import TelegramConfig
from app.models.telegram_bot import TelegramBotState, TelegramNotification
from app.services import jobs, telegram, telegram_bot, telegram_runtime


@pytest.fixture
def wire(monkeypatch):
    calls = []
    async def fake_call(token, method, body=None, **kwargs):
        calls.append((method, body, kwargs))
        if method == "getMe":
            return {"is_bot": True, "username": "layla_test_bot"}
        if method == "getWebhookInfo":
            return {"url": ""}
        if method == "getUpdates":
            return []
        return {"message_id": len(calls)}
    monkeypatch.setattr(telegram, "call", fake_call)
    return calls


def update(text="/menu", *, user=42, chat_type="private", id=1, callback=None):
    message = {"chat": {"id": user, "type": chat_type}, "from": {"id": user}, "text": text}
    if callback:
        return {"update_id": id, "callback_query": {"id": "q" + str(id), "from": {"id": user}, "data": callback,
                                                    "message": {**message, "from": {"id": 999, "is_bot": True}}}}
    return {"update_id": id, "message": message}


async def setup(client, maker):
    await client.post("/api/auth/register", json={"email": "bot@example.com", "password": "hunter2hunter2"})
    response = await client.put("/api/integrations/telegram", json={"bot_token": "123:private-token"})
    assert response.status_code == 200
    async with maker() as session:
        cfg = await session.scalar(select(TelegramConfig))
        config_id, owner_id = cfg.id, cfg.owner_id
    paired = await client.post("/api/integrations/telegram/pair")
    assert paired.status_code == 200
    code = parse_qs(urlsplit(paired.json()["url"]).query)["start"][0]
    await telegram_bot.handle_update(maker, config_id, update("/start " + code))
    return config_id, owner_id, code


def last_message(wire):
    return next(body for method, body, _ in reversed(wire) if method == "sendMessage")


@pytest.mark.asyncio
async def test_pair_link_is_private_expiring_and_one_use(client, db_sessionmaker, wire):
    cfg_id, owner, code = await setup(client, db_sessionmaker)
    status = (await client.get("/api/integrations/telegram")).json()
    assert status["connected"]
    assert status["default_chat_id"] == "42"
    assert "private-token" not in str(status)
    async with db_sessionmaker() as session:
        bot = await session.scalar(select(TelegramBotState))
        assert "pair_hash" not in bot.state
        assert code not in str(bot.state)
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update("/start " + code, user=43))
    assert "Подключите аккаунт" in last_message(wire)["text"]
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update("/tasks", user=42, chat_type="group"))
    assert (await client.get("/api/integrations/telegram")).json()["default_chat_id"] == "42"


@pytest.mark.asyncio
async def test_expired_pair_rejected(client, db_sessionmaker, wire):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    link = (await client.post("/api/integrations/telegram/pair")).json()["url"]
    code = parse_qs(urlsplit(link).query)["start"][0]
    async with db_sessionmaker() as session:
        bot = await session.scalar(select(TelegramBotState))
        bot.state = {**bot.state, "pair_expires": 0}
        await session.commit()
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update("/start " + code, user=43))
    async with db_sessionmaker() as session:
        assert (await session.scalar(select(TelegramBotState))).controller_user_id == "42"


@pytest.mark.asyncio
async def test_menu_callback_owner_guard_and_single_use(client, db_sessionmaker, wire):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    callback = last_message(wire)["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert len(callback.encode()) <= 64
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=callback, user=43))
    assert wire[-1][0] == "answerCallbackQuery"
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=callback))
    assert "Выберите раздел" in last_message(wire)["text"]
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=callback))
    assert "Кнопка устарела" in last_message(wire)["text"]


@pytest.mark.asyncio
async def test_models_and_domain_navigation(client, db_sessionmaker, wire):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    provider = (await client.post("/api/providers", json={"name": "QA", "kind": "openai_compatible",
        "base_url": "http://provider.local/v1", "default_model": "grok-test", "active": True})).json()
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update("/models"))
    callback = last_message(wire)["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=callback))
    async with db_sessionmaker() as session:
        bot = await session.scalar(select(TelegramBotState))
        assert bot.state["model"] == "grok-test"
        assert bot.state["provider_id"] == provider["id"]
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update("/domains"))
    callback = last_message(wire)["reply_markup"]["inline_keyboard"][1][0]["callback_data"]
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=callback))
    assert "Пентест" in last_message(wire)["text"]


@pytest.mark.asyncio
async def test_dispatch_uses_selected_project_provider_mode_and_request_id(client, db_sessionmaker, wire, monkeypatch):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    project = (await client.post("/api/projects", json={"name": "Site"})).json()
    async with db_sessionmaker() as session:
        bot = await session.scalar(select(TelegramBotState))
        bot.state = {"domain": "code", "target_id": project["id"], "target_name": "Site", "model": "test-model",
                     "provider_id": "test-provider", "mode": "confirm"}
        await session.commit()
    calls = []
    async def api(owner_id, method, path, body=None, **kw):
        calls.append((owner_id, method, path, body))
        return {"id": "chat-id" if path == "/chats" else "job-id"}
    monkeypatch.setattr(telegram_bot, "api_call", api)
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update("Проверь страницу", id=91))
    assert calls[0][0] == owner
    assert calls[0][3]["project_id"] == project["id"]
    assert calls[1][2] == "/chats/chat-id/run"
    assert calls[1][3]["mode"] == "confirm"
    assert calls[1][3]["provider_id"] == "test-provider"
    assert calls[1][3]["request_id"].endswith("-91")


@pytest.mark.asyncio
async def test_foreign_project_callback_still_uses_existing_owner_checks(client, db_sessionmaker, wire):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    client.cookies.clear()
    await client.post("/api/auth/register", json={"email": "other@example.com", "password": "hunter2hunter2"})
    project = (await client.post("/api/projects", json={"name": "Private"})).json()
    async with db_sessionmaker() as session:
        cfg = await session.get(TelegramConfig, cfg_id)
        bot = await session.scalar(select(TelegramBotState).where(TelegramBotState.config_id == cfg_id))
    panel = telegram_bot.Panel(db_sessionmaker, cfg, bot)
    button = panel.button("Project", "target", id=project["id"], domain="code")
    await panel.save()
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=button["callback_data"]))
    assert "недоступен" in last_message(wire)["text"]


@pytest.mark.asyncio
async def test_notifications_durable_and_idempotent(client, db_sessionmaker, wire):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        job = Job(owner_id=owner, domain="code", kind="task", title="From web", status="running")
        session.add(job)
        await session.commit()
        id = job.id
    await jobs._finish(db_sessionmaker, id, "done")
    await jobs._finish(db_sessionmaker, id, "done")
    async with db_sessionmaker() as session:
        notifications = list(await session.scalars(select(TelegramNotification)))
        assert len(notifications) == 1
        assert notifications[0].status == "queued"
    await telegram_runtime.deliver(db_sessionmaker)
    assert "Работа завершена" in last_message(wire)["text"]
    assert "reply_markup" in last_message(wire)
    before = len(wire)
    await telegram_runtime.deliver(db_sessionmaker)
    assert len(wire) == before


@pytest.mark.asyncio
async def test_rate_limit_retries_only_unsent_chunks(client, db_sessionmaker, wire, monkeypatch):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        cfg = await session.get(TelegramConfig, cfg_id)
        await telegram_runtime.queue(session, cfg, "test-long", "X" * 8000)
        await session.commit()
    count = 0
    async def fail_second(token, method, body=None, **kw):
        nonlocal count
        count += 1
        if count == 2:
            raise telegram.TelegramError("Telegram API: ошибка 429", retry_after=30)
        return {}
    monkeypatch.setattr(telegram, "call", fail_second)
    await telegram_runtime.deliver(db_sessionmaker)
    async with db_sessionmaker() as session:
        row = await session.scalar(select(TelegramNotification))
        assert row.sent_chunks == 1 and row.status == "queued"
        assert row.next_attempt >= time.time() + 20
        row.next_attempt = 0
        await session.commit()
    await telegram_runtime.deliver(db_sessionmaker)
    assert count == 4  # one sent, one failed, then the two remaining chunks
    async with db_sessionmaker() as session:
        assert (await session.scalar(select(TelegramNotification))).status == "sent"


@pytest.mark.asyncio
async def test_approval_notifications_are_not_duplicated(client, db_sessionmaker, wire):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        session.add(Job(owner_id=owner, domain="code", kind="task", title="Edit", status="running",
            result={"approval": {"id": "decision-1", "name": "edit", "path": "index.html"}}))
        await session.commit()
    await telegram_runtime.scan_approvals(db_sessionmaker)
    await telegram_runtime.scan_approvals(db_sessionmaker)
    async with db_sessionmaker() as session:
        assert len(list(await session.scalars(select(TelegramNotification)))) == 1


@pytest.mark.asyncio
async def test_disabled_notification_and_changed_destination(client, db_sessionmaker, wire):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        bot = await session.scalar(select(TelegramBotState))
        bot.notify_done = False
        job = Job(owner_id=owner, domain="code", kind="task", title="Quiet", status="done")
        session.add(job)
        await session.commit()
        await telegram_runtime.enqueue_finished(session, job)
        await session.commit()
        assert list(await session.scalars(select(TelegramNotification))) == []
        cfg = await session.get(TelegramConfig, cfg_id)
        await telegram_runtime.queue(session, cfg, "old", "private")
        cfg.default_chat_id = "43"
        await session.commit()
    before = len(wire)
    await telegram_runtime.deliver(db_sessionmaker)
    assert len(wire) == before
    async with db_sessionmaker() as session:
        assert (await session.scalar(select(TelegramNotification))).status == "cancelled"


@pytest.mark.asyncio
async def test_poll_offset_prevents_duplicate_task_dispatch(client, db_sessionmaker, wire, monkeypatch):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    processed = []
    async def transport(token, method, body=None, **kw):
        if method == "getUpdates":
            return [update("hello", id=100)]
        return {}
    async def handle(maker, cfg, item):
        processed.append(item["update_id"])
    monkeypatch.setattr(telegram, "call", transport)
    monkeypatch.setattr(telegram_bot, "handle_update", handle)
    await telegram_runtime.poll_once(db_sessionmaker, cfg_id)
    await telegram_runtime.poll_once(db_sessionmaker, cfg_id)
    assert processed == [100]
    async with db_sessionmaker() as session:
        assert (await session.scalar(select(TelegramBotState))).offset == 101


@pytest.mark.asyncio
async def test_disconnect_and_duplicate_bot_token(client, db_sessionmaker, wire):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    await client.post("/api/integrations/telegram/disconnect")
    assert not (await client.get("/api/integrations/telegram")).json()["connected"]
    client.cookies.clear()
    await client.post("/api/auth/register", json={"email": "other@example.com", "password": "hunter2hunter2"})
    response = await client.put("/api/integrations/telegram", json={"bot_token": "123:private-token"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_webhook_is_not_silently_removed(client, db_sessionmaker, wire, monkeypatch):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    async def transport(token, method, body=None, **kw):
        return {"url": "https://existing.example/webhook"} if method == "getWebhookInfo" else {"is_bot": True, "username": "layla_test_bot"}
    monkeypatch.setattr(telegram, "call", transport)
    assert (await client.post("/api/integrations/telegram/pair")).status_code == 409


@pytest.mark.asyncio
async def test_transport_hides_token_and_checks_api_ok(monkeypatch):
    real = httpx.AsyncClient
    def handle(request):
        return httpx.Response(200, json={"ok": False, "error_code": 401, "description": "secret"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(telegram.TelegramError) as error:
        await telegram.send_message("very-secret-token", "42", "test")
    assert "very-secret-token" not in str(error.value)
    assert "secret" not in str(error.value)
    parts = telegram.chunks("😀" * 5000)
    assert all(len(p.encode("utf-16-le")) // 2 <= 4096 for p in parts)


@pytest.mark.asyncio
async def test_notification_callbacks_do_not_overwrite_selection_or_revive_consumed_button(client, db_sessionmaker, wire):
    cfg_id, _, _ = await setup(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        cfg = await session.get(TelegramConfig, cfg_id)
        bot = await session.scalar(select(TelegramBotState))
    foreground = telegram_bot.Panel(db_sessionmaker, cfg, bot)
    old_button = foreground.button("Old", "menu")
    await foreground.save()
    async with db_sessionmaker() as session:
        bot = await session.scalar(select(TelegramBotState))
    background = telegram_bot.Panel(db_sessionmaker, cfg, bot)
    foreground.state["domain"] = "pentest"
    foreground.state["target_id"] = "new-selection"
    await foreground.save()
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=old_button["callback_data"]))
    new_button = background.button("Task", "tasks")
    await background.save()
    async with db_sessionmaker() as session:
        state = (await session.scalar(select(TelegramBotState))).state
        assert state["domain"] == "pentest"
        assert state["target_id"] == "new-selection"
        assert old_button["callback_data"][2:] not in state["callbacks"]
        assert new_button["callback_data"][2:] in state["callbacks"]


@pytest.mark.asyncio
async def test_stop_requires_explicit_confirmation(client, db_sessionmaker, wire, monkeypatch):
    cfg_id, owner, _ = await setup(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        cfg = await session.get(TelegramConfig, cfg_id)
        bot = await session.scalar(select(TelegramBotState))
    panel = telegram_bot.Panel(db_sessionmaker, cfg, bot)
    requests = []
    async def fake_api(owner_id, method, path, body=None, **kw):
        requests.append((method, path))
        return {"id": "job1"}
    monkeypatch.setattr(telegram_bot, "api_call", fake_api)
    button = panel.button("Stop", "stop_job", id="job1")
    await panel.save()
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=button["callback_data"]))
    assert requests == [("GET", "/jobs/job1")]
    confirmation = last_message(wire)["reply_markup"]["inline_keyboard"][0][0]
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=confirmation["callback_data"]))
    assert requests[-1] == ("POST", "/jobs/job1/cancel")


@pytest.mark.asyncio
async def test_project_file_browser_download_and_traversal_guard(client, db_sessionmaker, wire):
    cfg_id, _, _ = await setup(client, db_sessionmaker)
    project = (await client.post("/api/projects", json={"name": "Files"})).json()
    response = await client.put(f"/api/projects/{project['id']}/file",
        json={"path": "hello.txt", "content": "copy this", "expected_sha256": None})
    assert response.status_code == 200
    async with db_sessionmaker() as session:
        cfg = await session.get(TelegramConfig, cfg_id)
        bot = await session.scalar(select(TelegramBotState))
    panel = telegram_bot.Panel(db_sessionmaker, cfg, bot)
    panel.state.update(domain="code", target_id=project["id"])
    await panel.save()
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update("/files"))
    buttons = last_message(wire)["reply_markup"]["inline_keyboard"]
    file_button = next(row[0] for row in buttons if row[0]["text"].endswith("hello.txt"))
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update(callback=file_button["callback_data"]))
    assert next(kwargs for method, body, kwargs in reversed(wire) if method == "sendDocument")["files"]["document"][1] == b"copy this"
    sent_documents = sum(method == "sendDocument" for method, _, _ in wire)
    await telegram_bot.handle_update(db_sessionmaker, cfg_id, update("/file ../secret.txt"))
    assert sum(method == "sendDocument" for method, _, _ in wire) == sent_documents
