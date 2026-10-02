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
    await client.put(f"/api/engagements/{eid}/offensive", json={"enabled": True})
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


async def test_changed_host_is_rejected_before_password_is_sent():
    passwords = []
    class RecordingServer(_Server):
        def validate_password(self, username, password):
            passwords.append(password)
            return super().validate_password(username, password)
    trusted = asyncssh.generate_private_key('ssh-ed25519').export_public_key().decode()
    presented = asyncssh.generate_private_key('ssh-ed25519')
    server = await asyncssh.create_server(RecordingServer, '127.0.0.1', 0,
                                          server_host_keys=[presented], process_factory=_handle)
    async with server:
        with pytest.raises(ssh_exec.HostKeyChanged):
            await ssh_exec.probe(_cfg(server.get_port(), host_key=trusted))
    assert passwords == [], 'Password must not be offered to a server with a changed host key'


async def test_invalid_stored_host_key_fails_closed():
    server, port, _ = await _start_server()
    async with server:
        with pytest.raises(ssh_exec.HostKeyChanged):
            await ssh_exec.probe(_cfg(port, host_key='corrupted-pin'))


async def test_step_is_not_executed_twice(client, db_sessionmaker, monkeypatch):
    from app.models.agent import AgentRun, AgentStep
    server, port, _ = await _start_server()
    async with server:
        eid, _ = await _authorized_engagement_with_box(client, db_sessionmaker, port)
        async with db_sessionmaker() as session:
            user = await session.scalar(select(User).where(User.email == 'ssh@example.com'))
            run = AgentRun(engagement_id=eid, owner_id=user.id, task='once', status='paused')
            session.add(run)
            await session.flush()
            step = AgentStep(run_id=run.id, ordinal=0, role='implementer', kind='command',
                             target='127.0.0.1', command='echo once', status='awaiting_approval')
            session.add(step)
            await session.commit()
            step_id = step.id
        calls = []
        original = ssh_exec.run
        async def counting(*args, **kwargs):
            calls.append(args[1])
            return await original(*args, **kwargs)
        monkeypatch.setattr(ssh_exec, 'run', counting)
        first = await client.post(f'/api/agent/steps/{step_id}/approve')
        assert first.status_code == 200 and first.json()['status'] == 'done'
        again = await client.post(f'/api/agent/steps/{step_id}/approve')
        assert again.status_code in (200, 409)
        assert calls == ['echo once']


async def test_nonzero_exit_is_reported_as_failure(monkeypatch):
    from app.api.agent import _attack_box_runner
    from app.security import crypto
    server = Server(host='localhost', port=22, user='op', auth='password',
                    secret_ref=crypto.encrypt('pw'))
    async def unsuccessful(*args, **kwargs):
        return ssh_exec.SSHResult(exit_code=7, output='command failed', truncated=False, host_key='pin')
    monkeypatch.setattr(ssh_exec, 'run', unsuccessful)
    runner, _ = _attack_box_runner(server)
    with pytest.raises(ssh_exec.SSHError, match='7'):
        await runner('false')


async def _pending_command(client, maker, command='echo test'):
    from app.models.agent import AgentRun, AgentStep
    eid, _ = await _authorized_engagement_with_box(client, maker, 1)
    async with maker() as session:
        user = await session.scalar(select(User).where(User.email == 'ssh@example.com'))
        run = AgentRun(engagement_id=eid, owner_id=user.id, task='test', status='paused')
        session.add(run)
        await session.flush()
        step = AgentStep(run_id=run.id, ordinal=0, role='implementer', kind='command',
                         target='127.0.0.1', command=command, status='awaiting_approval')
        session.add(step)
        await session.commit()
        return run.id, step.id


async def test_parallel_approval_and_denial_cannot_duplicate_or_hide_running_command(client, db_sessionmaker, monkeypatch):
    import asyncio
    run_id, step_id = await _pending_command(client, db_sessionmaker)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def slow_run(*args, **kwargs):
        calls.append(args[1])
        entered.set()
        await release.wait()
        return ssh_exec.SSHResult(exit_code=0, output='done', truncated=False, host_key='pin')
    monkeypatch.setattr(ssh_exec, 'run', slow_run)
    first = asyncio.create_task(client.post(f'/api/agent/steps/{step_id}/approve'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await client.post(f'/api/agent/steps/{step_id}/approve')).status_code == 409
        assert (await client.post(f'/api/agent/steps/{step_id}/deny')).status_code == 409
        assert (await client.post(f'/api/agent/runs/{run_id}/stop')).status_code == 409
    finally:
        release.set()
        result = await first
    assert result.json()['status'] == 'done'
    assert calls == ['echo test']


async def test_failed_command_stays_failed_in_api(client, db_sessionmaker, monkeypatch):
    _, step_id = await _pending_command(client, db_sessionmaker, 'false')
    async def failure(*args, **kwargs):
        return ssh_exec.SSHResult(exit_code=23, output='failure details', truncated=False, host_key='pin')
    monkeypatch.setattr(ssh_exec, 'run', failure)
    result = await client.post(f'/api/agent/steps/{step_id}/approve')
    assert result.status_code == 200
    assert result.json()['status'] == 'failed'
    assert '23' in result.json()['output']


async def test_stopped_run_cannot_execute_an_old_pending_step(client, db_sessionmaker, monkeypatch):
    run_id, step_id = await _pending_command(client, db_sessionmaker)
    async def forbidden(*args, **kwargs):
        pytest.fail('Stopped run must not execute commands')
    monkeypatch.setattr(ssh_exec, 'run', forbidden)
    assert (await client.post(f'/api/agent/runs/{run_id}/stop')).status_code == 200
    assert (await client.post(f'/api/agent/steps/{step_id}/approve')).status_code == 409
