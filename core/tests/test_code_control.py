"""User plan decisions, recovery/compaction and shared budget admission."""
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.models.chat import Message
from app.models.code_control import CodeContextView, CodePlan, CodeUsageCall
from app.models.code_session import CodeLeadEntry
from app.models.code_workspace import CodeWorkspace
from app.models.provider import Provider
from app.services import (
    code_context,
    code_sessions,
    code_usage,
    history,
    project_agent,
    tool_chat,
)
from tests.test_code_runner import _script, _wait
from tests.test_code_workers import setup


async def run(client, chat, content="Task", **body):
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": content, **body})
    assert response.status_code == 202, response.text
    job = await _wait(client, response.json()["id"], lambda row: row["status"] not in ("queued", "running"))
    return job


async def controls(client, chat):
    response = await client.get(f"/api/chats/{chat['id']}/code-control")
    assert response.status_code == 200, response.text
    return response.json()


async def test_plan_submission_requires_explicit_approval_and_is_idempotent(client, db_sessionmaker, monkeypatch):
    _manager, chat, root = await setup(client, db_sessionmaker)
    _script(monkeypatch, [[{"name": "submit_plan", "args": {"title": "Write result", "plan": "1. Create result.txt. 2. Verify it."}},
                           {"name": "write_file", "args": {"path": "bad.txt", "content": "must not run", "expected_sha256": None}}]])
    assert (await run(client, chat, "Plan the change", mode="plan"))["status"] == "done"
    assert not (root / "bad.txt").exists()
    plan = (await controls(client, chat))["plans"][0]
    assert plan["status"] == "pending" and plan["content"].startswith("1. Create")
    seen = []
    _script(monkeypatch, [[{"name": "write_file", "args": {"path": "result.txt", "content": "done", "expected_sha256": None}}]], seen)
    job = await run(client, chat, "Execute approved plan", mode="auto", approved_plan_id=plan["id"], request_id="approval-once")
    assert job["status"] == "done" and (root / "result.txt").read_text() == "done"
    assert any(item["role"] == "system" and plan["content"] in item["content"]
               for item in seen[0]["conversation"])
    retry = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Execute approved plan", "mode": "auto",
        "approved_plan_id": plan["id"], "request_id": "approval-once"})
    assert retry.status_code == 202 and retry.json()["id"] == job["id"]
    assert (await controls(client, chat))["plans"][0]["status"] == "completed"
    assert (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "again", "approved_plan_id": plan["id"]})).status_code == 409
    async with db_sessionmaker() as session:
        assert len(list(await session.scalars(select(CodePlan)))) == 1
        assert any('Plan transition' in json.dumps(row.message) for row in await session.scalars(select(CodeLeadEntry)))


async def test_agent_enter_plan_mode_blocks_rest_of_batch_and_user_can_reject(client, db_sessionmaker, monkeypatch):
    _manager, chat, root = await setup(client, db_sessionmaker)
    _script(monkeypatch, [[{"name": "enter_plan_mode", "args": {"reason": "Need design approval"}},
                           {"name": "write_file", "args": {"path": "oops", "content": "no", "expected_sha256": None}}],
                         [{"name": "submit_plan", "args": {"title": "Review design", "plan": "Inspect then update the parser."}}]])
    job = await run(client, chat, "Design first")
    assert job["status"] == "done" and job["result"]["mode"] == "plan" and not (root / "oops").exists()
    plan = (await controls(client, chat))["plans"][0]
    rejected = await client.post(f"/api/chats/{chat['id']}/code-control/plans/{plan['id']}/reject", json={"feedback": "Keep compatibility"})
    assert rejected.status_code == 200 and rejected.json()["feedback"] == "Keep compatibility"
    assert (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "execute", "approved_plan_id": plan["id"]})).status_code == 409


async def test_plan_stale_new_instructions_workspace_and_owner_do_not_grant_execution(client, db_sessionmaker, monkeypatch):
    manager, chat, _root = await setup(client, db_sessionmaker)
    _script(monkeypatch, [])
    await run(client, chat, "Plan", mode="plan")  # legacy textual Plan output also becomes a proposal
    plan = (await controls(client, chat))["plans"][0]
    async with db_sessionmaker() as session:
        session.add(CodeWorkspace(owner_id=manager.owner_id, chat_id=chat["id"], project_id=manager.project_id,
            source_root=manager.root, name="other", branch="other", status="active", active_slot=1))
        await session.commit()
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "execute", "approved_plan_id": plan["id"]})
    assert response.status_code == 409 and "копия" in response.text
    async with db_sessionmaker() as session:
        workspace = await session.scalar(select(CodeWorkspace))
        workspace.active_slot = None
        session.add(Message(chat_id=chat["id"], role="user", content="New constraint", created_at=datetime.now(UTC)))
        await session.commit()
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "execute", "approved_plan_id": plan["id"]})
    assert response.status_code == 409 and "новые" in response.text
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "not-plan-owner@example.com", "password": "hunter2hunter2"})
    assert (await client.get(f"/api/chats/{chat['id']}/code-control")).status_code == 404


async def test_recovery_survives_cancel_and_validated_resume_does_not_repeat_write(client, db_sessionmaker, monkeypatch):
    manager, chat, root = await setup(client, db_sessionmaker)
    parked = asyncio.Event()
    count = 0
    async def stream(_p, _k, _m, messages, tools, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            yield "tool_calls", [{"id": "saved-write", "type": "function", "function": {"name": "write_file",
                "arguments": '{"path":"once.txt","content":"once","expected_sha256":null}'}}]
        elif count == 2:
            parked.set()
            await asyncio.Event().wait()
        else:
            assert any(item.get("tool_call_id") == "saved-write" for item in messages)
            yield "content", "Continued from saved progress"
            yield "usage", {"input_tokens": 100, "output_tokens": 10}
    monkeypatch.setattr(tool_chat, "stream_turn", stream)
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Write and verify"})
    await asyncio.wait_for(parked.wait(), 5)
    await client.post(f"/api/jobs/{response.json()['id']}/cancel")
    saved = (await controls(client, chat))["recovery"]
    assert saved["interrupted"] and any(event["id"].endswith("saved-write") for event in saved["tools"])
    assert (root / "once.txt").read_text() == "once"
    assert (await run(client, chat, "Continue", resume_message_id=saved["message_id"]))["status"] == "done"
    assert (root / "once.txt").read_text() == "once"
    usage = await code_usage.snapshot(manager.runtime)
    assert usage["calls"]["uncertain"] == 1 and usage["calls"]["reported"] == 1
    assert (await client.post(f"/api/chats/{chat['id']}/run", json={"content": "again", "resume_message_id": saved["message_id"]})).status_code == 409


async def test_memory_consolidation_keeps_notes_and_rejects_concurrent_change(client, db_sessionmaker, monkeypatch):
    manager, _chat, _root = await setup(client, db_sessionmaker)
    await manager.runtime.call("memory_write", {"key": "decision", "content": "Use SQLite in tests"})
    async def summarize(*args, **kwargs):
        return "[decision] SQLite is used in tests."
    monkeypatch.setattr(history, "summarize", summarize)
    result = await code_context.consolidate(manager.runtime, None, None, "m", {})
    assert result["sources"] == 1 and "decision" in (await manager.runtime.read())["memory"]
    assert "consolidated-memory" in await manager.runtime.context()
    await manager.runtime.call("memory_write", {"key": "decision", "content": "Use Postgres for acceptance"})
    assert "consolidated-memory" not in await manager.runtime.context()
    async def concurrent(*args, **kwargs):
        await manager.runtime.call("memory_write", {"key": "new", "content": "New fact"})
        return "Stale summary"
    monkeypatch.setattr(history, "summarize", concurrent)
    with pytest.raises(ValueError, match="изменилась"):
        await code_context.consolidate(manager.runtime, None, None, "m", {})
    assert (await manager.runtime.read())["memory"]["new"]["content"] == "New fact"


async def test_compact_view_survives_new_transcript_and_raw_evidence_can_be_recalled(client, db_sessionmaker, monkeypatch):
    manager, chat, _root = await setup(client, db_sessionmaker)
    async def create_turn(text):
        async with db_sessionmaker() as session:
            source = Message(chat_id=chat["id"], role="user", content=text, created_at=datetime.now(UTC))
            result = Message(chat_id=chat["id"], role="assistant", content="", created_at=datetime.now(UTC))
            session.add_all([source, result]); await session.commit()
        transcript = code_sessions.LeadTranscript(manager.runtime, result.id, source.id, provider_id=manager.provider_id, model="m")
        messages = await transcript.load([{"role": "user", "content": text}])
        return transcript, transcript.conversation(messages)
    first, conversation = await create_turn("Original task")
    conversation.append({"role": "assistant", "content": "RAW-EVIDENCE " * 2000})
    conversation.append({"role": "user", "content": "Continue"})
    anchor = conversation[-1]
    await first.flush(conversation)
    async def summarize(*args, **kwargs): return "COMPACTED-DECISION"
    monkeypatch.setattr(history, "summarize", summarize)
    assert await code_context.compact_run(manager.runtime, conversation, anchor, None, None, "m", {"context": 5000, "max_output": 100}, first)
    await first.flush(conversation)
    async with db_sessionmaker() as session:
        assert await session.get(CodeContextView, first.turn_id)
    _second, replay = await create_turn("Next task")
    encoded = json.dumps(replay)
    assert "COMPACTED-DECISION" in encoded and "RAW-EVIDENCE" not in encoded
    recalled = await code_context.search(manager.runtime, code_context.Search(query="RAW-EVIDENCE"))
    assert recalled["entries"] and recalled["entries"][0]["turn_id"] == first.turn_id


async def test_budget_atomic_admission_and_unknown_reservation_survives_restart(client, db_sessionmaker):
    manager, chat, _root = await setup(client, db_sessionmaker)
    await code_usage.configure(manager.runtime, code_usage.Policy(max_tokens=10000, max_usd=Decimal("0.02"),
        input_usd_per_million=Decimal(1), output_usd_per_million=Decimal(2)), manager.provider_id, "m")
    account = code_usage.Account(manager.runtime)
    requests = await asyncio.gather(*(account.reserve(manager.provider_id, "m", [{"role": "user", "content": "hi"}], [], {})
                                     for _ in range(2)), return_exceptions=True)
    assert sum(isinstance(item, str) for item in requests) == 1
    assert any(isinstance(item, code_usage.BudgetExceeded) for item in requests)
    call_id = next(item for item in requests if isinstance(item, str))
    await account.settle(call_id, failed=True)
    before = await code_usage.snapshot(manager.runtime)
    assert before["calls"]["uncertain"] == 1
    with pytest.raises(code_usage.BudgetExceeded):
        await code_usage.Account(manager.runtime).reserve(manager.provider_id, "m", [], [], {})
    await code_usage.configure(manager.runtime, code_usage.Policy(max_tokens=50000), manager.provider_id, "m")
    call = await account.reserve(manager.provider_id, "m", [], [], {})
    await asyncio.gather(account.settle(call, {"input_tokens": 10, "output_tokens": 20}), account.settle(call, {"input_tokens": 10, "output_tokens": 20}))
    after = await code_usage.snapshot(manager.runtime)
    assert after["tokens_used"] == before["tokens_used"] + 30
    assert after["calls"] == {"uncertain": 1, "reported": 1}
    # API reads preserve usage; configuration cannot reset it.
    assert (await controls(client, chat))["usage"]["tokens_used"] == after["tokens_used"]


@pytest.mark.parametrize("native", [False, True])
async def test_provider_usage_is_normalized_and_charged_only_in_code_scope(client, db_sessionmaker, monkeypatch, native):
    manager, _chat, _root = await setup(client, db_sessionmaker)
    async with db_sessionmaker() as session:
        provider = await session.get(Provider, manager.provider_id)
        if native:
            from app.models.enums import ProviderKind
            provider.kind = ProviderKind.anthropic
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        if native:
            events = [{"type": "message_start", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 0}}},
                {"type": "content_block_delta", "index": 0, "delta": {"text": "done"}},
                {"type": "message_delta", "usage": {"output_tokens": 3}, "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"}]
        else:
            events = [{"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 15, "completion_tokens": 3}}]
        return httpx.Response(200, text="".join("data: " + json.dumps(item) + "\n\n" for item in events) + ("" if native else "data: [DONE]\n\n"))
    original = httpx.AsyncClient
    monkeypatch.setattr(tool_chat.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    async with code_usage.tracking(manager.runtime):
        events = [item async for item in project_agent._turn(provider, "key", "m", [{"role": "user", "content": "hi"}], [], {})]
    assert events[-1][0] == "done"
    assert (await code_usage.snapshot(manager.runtime))["tokens_used"] == 18
    async with db_sessionmaker() as session:
        row = await session.scalar(select(CodeUsageCall))
        assert row.status == "reported" and row.input_tokens == 15 and row.output_tokens == 3
    assert native or seen[0]["stream_options"] == {"include_usage": True}


async def test_confirm_plan_still_waits_for_file_approval(client, db_sessionmaker, monkeypatch):
    _manager, chat, root = await setup(client, db_sessionmaker)
    _script(monkeypatch, [])
    await run(client, chat, "Plan", mode="plan")
    plan = (await controls(client, chat))["plans"][0]
    _script(monkeypatch, [[{"name": "write_file", "args": {"path": "approved.txt", "content": "yes", "expected_sha256": None}}]])
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Execute", "mode": "confirm", "approved_plan_id": plan["id"]})
    assert response.status_code == 202, response.text
    job = await _wait(client, response.json()["id"], lambda row: bool(row["result"].get("approval")))
    assert not (root / "approved.txt").exists()
    assert (await controls(client, chat))["plans"][0]["status"] == "executing"
    decision = await client.post(f"/api/jobs/{job['id']}/decision", json={"approval_id": job["result"]["approval"]["id"], "decision": "approve"})
    assert decision.status_code == 200
    done = await _wait(client, job["id"], lambda row: row["status"] not in ("queued", "running"))
    assert done["status"] == "done" and (root / "approved.txt").read_text() == "yes"


async def test_budget_denial_sends_no_request_and_exposes_recoverable_reason(client, db_sessionmaker, monkeypatch):
    manager, chat, _root = await setup(client, db_sessionmaker)
    await code_usage.configure(manager.runtime, code_usage.Policy(max_tokens=1), manager.provider_id, "m")
    requests = []
    async def stream(*args, **kwargs):
        requests.append(True)
        yield "content", "must never run"
    monkeypatch.setattr(tool_chat, "stream_turn", stream)
    job = await run(client, chat, "Work")
    assert job["status"] == "error" and "Бюджет" in job["error"] and not requests
    state = await controls(client, chat)
    assert state["recovery"]["interrupted"] and "Бюджет" in state["recovery"]["error"]
    assert state["usage"]["tokens_used"] == 0


async def test_uncertain_retry_cannot_bypass_budget(client, db_sessionmaker, monkeypatch):
    from app.services import provider_errors
    manager, _chat, _root = await setup(client, db_sessionmaker)
    await code_usage.configure(manager.runtime, code_usage.Policy(max_tokens=10000), manager.provider_id, "m")
    sent = []
    async def stream(*args, **kwargs):
        sent.append(True)
        yield "content", "partial"
        raise provider_errors.ProviderError("network interrupted", retryable=True)
    monkeypatch.setattr(tool_chat, "stream_turn", stream)
    async with db_sessionmaker() as session:
        provider = await session.get(Provider, manager.provider_id)
    async with code_usage.tracking(manager.runtime):
        with pytest.raises(code_usage.BudgetExceeded):
            _ = [event async for event in project_agent._turn(provider, "key", "m", [{"role": "user", "content": "hi"}], [], {})]
    assert len(sent) == 1 and (await code_usage.snapshot(manager.runtime))["calls"]["uncertain"] == 1


async def test_independent_worker_uses_same_chat_budget(client, db_sessionmaker, monkeypatch):
    from app.services.code_workers import AgentStart
    from tests.test_code_workers import terminal
    manager, _chat, _root = await setup(client, db_sessionmaker)
    await code_usage.configure(manager.runtime, code_usage.Policy(max_tokens=1), manager.provider_id, "m")
    sent = []
    async def stream(*args, **kwargs):
        sent.append(True)
        yield "content", "must never run"
    monkeypatch.setattr(tool_chat, "stream_turn", stream)
    result = await manager.start_agent(AgentStart(task="Implement a small function and verify it"))
    state = await terminal(manager, result["worker"]["id"])
    assert state["worker"]["status"] == "error" and "Бюджет" in state["worker"]["error"] and not sent


async def test_pending_plan_cannot_be_bypassed_by_plain_auto_message(client, db_sessionmaker, monkeypatch):
    _manager, chat, root = await setup(client, db_sessionmaker)
    _script(monkeypatch, [])
    await run(client, chat, "Plan", mode="plan")
    seen = []
    _script(monkeypatch, [[{"name": "write_file", "args": {"path": "unapproved.txt", "content": "bad", "expected_sha256": None}}]], seen)
    job = await run(client, chat, "Consider another detail", mode="auto")
    assert job["status"] == "done" and job["result"]["mode"] == "plan"
    assert "write_file" not in seen[0]["tools"] and not (root / "unapproved.txt").exists()
