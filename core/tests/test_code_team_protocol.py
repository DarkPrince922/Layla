"""Plan decisions, graceful shutdown and same-owner cross-chat peer mail."""
from sqlalchemy import select

from app.models.code_team import CodeTeamRequest
from app.models.code_worker import CodeWorker
from app.services import code_state, code_teams, code_workers
from tests.test_code_teams import dormant_team


async def test_plan_then_approval_and_graceful_shutdown(client, db_sessionmaker, monkeypatch):
    manager, chat, _root, ids = await dormant_team(client, db_sessionmaker, monkeypatch)
    worker = code_teams.Runtime(manager, ids[0])
    plan = await worker.call("team_request", {"kind": "plan", "to": "lead", "content": "Inspect, modify and run tests"})
    assert plan["request"]["status"] == "pending"
    assert (await worker.info())["requests"][0]["status"] == "pending"
    assert "error" in await worker.call("team_respond", {"request_id": plan["request"]["id"], "approve": True})
    decision = await manager.team.call("team_respond", {"request_id": plan["request"]["id"], "approve": True})
    assert decision["request"]["status"] == "approved"
    assert (await manager.team.call("team_respond", {"request_id": plan["request"]["id"], "approve": True}))["request"]["status"] == "approved"
    assert "error" in await manager.team.call("team_respond", {"request_id": plan["request"]["id"], "approve": False})
    shutdown = await manager.team.call("team_request", {"kind": "shutdown", "to": "builder", "content": "Finish safely"})
    assert not await worker.should_stop()
    assert "error" in await manager.team.call("team_respond", {"request_id": shutdown["request"]["id"], "approve": True})
    assert "error" not in await worker.call("team_respond", {"request_id": shutdown["request"]["id"], "approve": True})
    assert await worker.should_stop()
    async with db_sessionmaker() as session:
        assert (await session.scalar(select(CodeTeamRequest).where(CodeTeamRequest.id == shutdown["request"]["id"]))).status == "approved"
    assert (await client.get(f"/api/chats/{chat['id']}/team")).json()["requests"]


async def test_approved_plan_promotes_role_only_after_resume(client, db_sessionmaker, monkeypatch):
    manager, _chat, _root, _ids = await dormant_team(client, db_sessionmaker, monkeypatch)
    started = await manager.start_agent(code_workers.AgentStart(
        task="Inspect and modify this module", name="planner", role="implement", require_plan=True))
    ident = started["worker"]["id"]
    assert started["worker"]["role"] == "explore"
    worker = code_teams.Runtime(manager, ident)
    plan = await worker.call("team_request", {"kind": "plan", "to": "lead", "content": "Read, edit and test"})
    async with db_sessionmaker() as session:
        row = await session.get(CodeWorker, ident)
        row.status, row.slot = "interrupted", None
        await session.commit()
    assert (await manager.resume(ident))["worker"]["role"] == "explore"
    async with db_sessionmaker() as session:
        row = await session.get(CodeWorker, ident)
        row.status, row.slot = "interrupted", None
        await session.commit()
    await manager.team.call("team_respond", {"request_id": plan["request"]["id"], "approve": True})
    assert (await manager.resume(ident))["worker"]["role"] == "implement"


async def test_peer_mail_crosses_owned_code_chats_only(client, db_sessionmaker, monkeypatch):
    manager, chat, root, _ids = await dormant_team(client, db_sessionmaker, monkeypatch)
    other = (await client.post("/api/chats", json={"domain": "code", "project_id": manager.project_id, "model": "m"})).json()
    peer_manager = code_workers.Manager(code_state.Runtime(db_sessionmaker, manager.owner_id, other["id"], manager.project_id), str(root))
    assert "error" not in await peer_manager.team.call("team_create", {"team_name": "receiver"})
    sent = await manager.team.call("send_message", {"chat_id": other["id"], "to": "lead", "message": "Fact from first chat"})
    assert sent["sent_to_chat"] == other["id"]
    assert (await peer_manager.team.info())["messages"][0]["from"] == "lead@" + chat["id"]
    third = (await client.post("/api/chats", json={"domain": "code", "model": "m"})).json()
    assert "error" in await manager.team.call("send_message", {"chat_id": third["id"], "to": "lead", "message": "No"})
