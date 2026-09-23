"""SSH-исполнитель attack box: подключение, запуск, пиннинг ключа хоста, гейты."""
from __future__ import annotations

import asyncssh
import pytest
from sqlalchemy import select

from app.models.pentest import Server
from app.models.user import User
from app.services import ssh_exec

pytestmark = pytest.mark.asyncio


async def _handle(process: asyncssh.SSHServerProcess) -> None:
    cmd = process.command or "echo layla-ok"
    process.stdout.write(f"ran: {cmd}")
    process.exit(0)


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return True  # требуем аутентификацию

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return password == "pw"


async def _start_server():
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        _Server, "127.0.0.1", 0, server_host_keys=[host_key], process_factory=_handle,
    )
    port = server.sockets[0].getsockname()[1]
    return server, port, host_key


def _cfg(port: int, host_key: str | None = None) -> ssh_exec.SSHConfig:
    return ssh_exec.SSHConfig(host="127.0.0.1", port=port, user="op", auth="password",
                              secret="pw", host_key=host_key)


async def test_probe_and_run_over_ssh():
    server, port, _ = await _start_server()
    async with server:
        probe = await ssh_exec.probe(_cfg(port))
        assert probe.exit_code == 0 and "layla-ok" in probe.output and probe.host_key.startswith("ssh-")
        result = await ssh_exec.run(_cfg(port), "id -u")
        assert result.exit_code == 0 and result.output == "ran: id -u"


async def test_host_key_pinning_detects_change():
    server, port, _ = await _start_server()
    async with server:
        first = await ssh_exec.probe(_cfg(port))       # первое подключение — ключ ещё не закреплён
        # Совпадает с закреплённым — ок.
        again = await ssh_exec.probe(_cfg(port, host_key=first.host_key))
        assert again.exit_code == 0
    # Тот же порт, другой сервер → другой ключ хоста → отказ (возможен MITM).
    server2, port2, _ = await _start_server()
    async with server2:
        with pytest.raises(ssh_exec.HostKeyChanged):
            await ssh_exec.probe(_cfg(port2, host_key=first.host_key))


async def test_wrong_password_is_reported():
    server, port, _ = await _start_server()
    async with server:
        cfg = ssh_exec.SSHConfig(host="127.0.0.1", port=port, user="op", auth="password",
                                 secret="nope", host_key=None)
        with pytest.raises(ssh_exec.SSHError):
            await ssh_exec.probe(cfg)


async def test_connect_failure_is_wrapped():
    # Порт, где никто не слушает.
    with pytest.raises(ssh_exec.SSHError):
        await ssh_exec.run(_cfg(1), "echo x", timeout=3)


# ---- через API: гейты + HITL-исполнение на attack box ----

async def _authorized_engagement_with_box(client, db_sessionmaker, port: int):
    await client.post("/api/auth/register", json={"email": "ssh@example.com", "password": "hunter2hunter2"})
    e = (await client.post("/api/engagements", json={"target": "127.0.0.1"})).json()
    eid = e["id"]
    await client.put(f"/api/engagements/{eid}/scope", json={"allow": ["127.0.0.1"], "deny": []})
    await client.post(f"/api/engagements/{eid}/scope/confirm")
    await client.post(f"/api/engagements/{eid}/authorize")
    srv = (await client.post("/api/servers", json={
        "host": "127.0.0.1", "port": port, "user": "op", "auth": "password", "password": "pw",
    })).json()
    await client.put(f"/api/engagements/{eid}/venue",
                     json={"mode": "attack_box", "egress_route": "direct", "attack_box_id": srv["id"]})
    return eid, srv["id"]


async def test_test_connection_endpoint_pins_host_key(client, db_sessionmaker):
    server, port, _ = await _start_server()
    async with server:
        _, sid = await _authorized_engagement_with_box(client, db_sessionmaker, port)
        r = await client.post(f"/api/servers/{sid}/test-connection")
        assert r.status_code == 200 and r.json()["ok"] is True and r.json()["pinned_host_key"] is True
    async with db_sessionmaker() as session:
        s = await session.get(Server, sid)
        assert s.host_key and s.host_key.startswith("ssh-")  # ключ закреплён


async def test_agent_step_runs_on_attack_box(client, db_sessionmaker):
    server, port, _ = await _start_server()
    async with server:
        eid, _ = await _authorized_engagement_with_box(client, db_sessionmaker, port)
        # Заводим шаг-команду напрямую в БД (планировщик LLM тут не нужен).
        async with db_sessionmaker() as session:
            from app.models.agent import AgentRun, AgentStep
            user = await session.scalar(select(User).where(User.email == "ssh@example.com"))
            run = AgentRun(engagement_id=eid, owner_id=user.id, task="whoami", status="running")
            session.add(run)
            await session.flush()
            step = AgentStep(run_id=run.id, ordinal=0, role="implementer", kind="command",
                             target="127.0.0.1", command="whoami", status="awaiting_approval")
            session.add(step)
            await session.commit()
            sid = step.id
        r = await client.post(f"/api/agent/steps/{sid}/approve")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "done" and body["output"] == "ran: whoami"
