"""Песочница: синхронизация рабочей копии, запуск команд от uid пользователя, изоляция, прокси.

Запуск команд от чужого uid требует root — в контейнере сборки/CI тесты идут от root.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

import httpx
import pytest

from layla_sandbox import config, proxy, server
from layla_sandbox.workspace import Users, prepare_root

root_only = pytest.mark.skipif(os.geteuid() != 0, reason="нужен root: команды идут от uid песочницы")


@pytest.fixture
def work(monkeypatch):
    path = Path(tempfile.mkdtemp(prefix="layla-sandbox-", dir="/var/tmp"))
    os.chmod(path, 0o711)
    prepare_root(path)
    monkeypatch.setattr(config, "WORK", path)
    monkeypatch.setattr(server, "users", Users(path))
    monkeypatch.setattr(config, "PROXY", "")
    yield path
    shutil.rmtree(path, ignore_errors=True)


def tar(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://sandbox")


async def sync(http, owner, project, files: dict[str, str], marks: dict[str, str] | None = None):
    marks = marks or {name: f"{len(text)}:1" for name, text in files.items()}
    head = json.dumps({"files": marks}).encode() + b"\n"
    first = (await http.post(f"/workspaces/{owner}/{project}/sync", content=head)).json()
    if "need" not in first:
        return first
    body = head + tar({name: files[name] for name in first["need"]})
    return (await http.post(f"/workspaces/{owner}/{project}/sync", content=body)).json()


async def run(http, owner, project, command, **extra):
    response = await http.post(f"/workspaces/{owner}/{project}/run",
                               json={"command": command, **extra}, timeout=120)
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    output = "".join(e["data"] for e in events if e["type"] == "output")
    return output, events[-1]


@root_only
async def test_sync_and_run_in_project_copy(work):
    async with client() as http:
        result = await sync(http, "alice", "p1", {"main.py": "print('hi', 6*7)\n", "src/util.txt": "x"})
        assert result["ok"] and result["written"] == 2
        output, done = await run(http, "alice", "p1", "python main.py && cat src/util.txt")
        assert "hi 42" in output and output.endswith("x")
        assert done == {**done, "type": "exit", "code": 0, "timed_out": False}
        # Повторная синхронизация без изменений ничего не присылает.
        again = await sync(http, "alice", "p1", {"main.py": "print('hi', 6*7)\n", "src/util.txt": "x"})
        assert again["written"] == 0


@root_only
async def test_changed_and_deleted_files_follow_the_project(work):
    async with client() as http:
        await sync(http, "alice", "p1", {"a.txt": "one", "b.txt": "two"})
        # Команда испортила файл проекта в копии — следующая синхронизация его вернёт.
        await run(http, "alice", "p1", "echo broken > a.txt && mkdir -p build && echo o > build/out")
        await sync(http, "alice", "p1", {"a.txt": "one"})  # b.txt удалён из проекта
        output, _ = await run(http, "alice", "p1", "cat a.txt; ls; cat build/out")
        assert output.startswith("one") and "b.txt" not in output
        assert "build" in output  # сборки команд остаются в копии


@root_only
async def test_commands_run_as_separate_user_per_owner(work):
    async with client() as http:
        await sync(http, "alice", "p1", {"secret.txt": "alice-secret"})
        await sync(http, "bob", "p2", {"x.txt": "x"})
        alice_uid = server.users.uid("alice")
        output, _ = await run(http, "bob", "p2", f"id -u; cat {work}/u{alice_uid}/projects/p1/secret.txt; ls {work}")
        lines = output.splitlines()
        assert int(lines[0]) != alice_uid and int(lines[0]) >= config.UID_MIN
        assert "alice-secret" not in output and "Permission denied" in output


@root_only
async def test_symlink_in_copy_cannot_redirect_sync(work):
    target = work / "outside"
    target.mkdir()
    os.chmod(target, 0o755)
    async with client() as http:
        await sync(http, "alice", "p1", {"a.txt": "1"})
        await run(http, "alice", "p1", f"rm -rf src && ln -s {target} src")
        await sync(http, "alice", "p1", {"a.txt": "1", "src/evil.txt": "pwn"})
        assert not (target / "evil.txt").exists()
        output, _ = await run(http, "alice", "p1", "test -L src && echo link || cat src/evil.txt")
        assert output.strip() == "pwn"


@root_only
async def test_timeout_kills_the_command_and_its_children(work):
    async with client() as http:
        await sync(http, "alice", "p1", {"a.txt": "1"})
        output, done = await run(http, "alice", "p1", "(sleep 30 &) ; echo started; sleep 30", timeout=1)
        assert "started" in output and done["timed_out"] is True
        # Фоновый sleep тоже добит: живых процессов у пользователя не осталось (зомби подбирает init).
        output, _ = await run(http, "alice", "p1",
                              "ps -u $(id -u) -o stat=,comm= | grep -v '^Z' | grep -c sleep || true")
        assert output.strip() == "0"


@root_only
async def test_output_is_capped_and_stdin_is_passed(work, monkeypatch):
    monkeypatch.setattr(config, "MAX_OUTPUT", 1000)
    async with client() as http:
        await sync(http, "alice", "p1", {"a.txt": "1"})
        output, done = await run(http, "alice", "p1", "yes | head -c 50000")
        assert len(output) == 1000 and done["truncated"] is True and done["code"] == 0
        output, _ = await run(http, "alice", "p1", "read name; echo hello $name", stdin="Layla\n")
        assert output.strip() == "hello Layla"


@root_only
async def test_python_venv_is_ready_for_pip(work):
    async with client() as http:
        await sync(http, "alice", "p1", {"a.txt": "1"})
        output, done = await run(http, "alice", "p1", "which python pip; echo $VIRTUAL_ENV")
        assert done["code"] == 0 and "/.venv/bin/python" in output and "/.venv/bin/pip" in output


@root_only
async def test_reset_removes_the_copy(work):
    async with client() as http:
        await sync(http, "alice", "p1", {"a.txt": "1"})
        await run(http, "alice", "p1", "echo dep > dep.txt")
        assert (await http.delete("/workspaces/alice/p1")).json() == {"ok": True}
        again = await sync(http, "alice", "p1", {"a.txt": "1"})
        assert again["written"] == 1
        output, _ = await run(http, "alice", "p1", "ls")
        assert "dep.txt" not in output


async def test_bad_ids_and_paths_are_rejected(work):
    async with client() as http:
        assert (await http.post("/workspaces/..%2Fx/p/sync", content=b"{}\n")).status_code in (400, 404)
        bad = json.dumps({"files": {"../etc/passwd": "1:1"}}).encode() + b"\n"
        assert (await http.post("/workspaces/alice/p1/sync", content=bad)).status_code == 400


def test_proxy_host_rules():
    hosts = proxy.DEFAULT_HOSTS
    assert proxy.host_allowed("files.pythonhosted.org", hosts)
    assert proxy.host_allowed("registry.npmjs.org.", hosts)
    assert not proxy.host_allowed("evil.com", hosts)
    assert not proxy.host_allowed("pypi.org.evil.com", hosts)
    assert proxy.host_allowed("anything.example", ("*",))
    assert not proxy.public_ip("127.0.0.1") and not proxy.public_ip("10.0.0.5")
    assert not proxy.public_ip("169.254.169.254") and not proxy.public_ip("172.18.0.2")
    assert proxy.public_ip("151.101.0.223")


async def _ask(port: int, line: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(line.encode() + b"\r\n\r\n")
    await writer.drain()
    data = await reader.read(1024)
    writer.close()
    return data


async def test_proxy_denies_other_hosts_private_addresses_and_plain_http():
    server_ = await asyncio.start_server(
        lambda r, w: proxy.handle(r, w, ("localhost", "pypi.org")), "127.0.0.1", 0)
    port = server_.sockets[0].getsockname()[1]
    async with server_:
        assert b"403" in await _ask(port, "CONNECT evil.com:443 HTTP/1.1")
        assert b"403" in await _ask(port, "CONNECT pypi.org:22 HTTP/1.1")
        # Разрешённое имя, но указывает во внутреннюю сеть — закрыто.
        assert b"private" in await _ask(port, "CONNECT localhost:443 HTTP/1.1")
        assert b"405" in await _ask(port, "GET http://pypi.org/ HTTP/1.1")
