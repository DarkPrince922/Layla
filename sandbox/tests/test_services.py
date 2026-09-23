"""Превью приложения: долгоживущий процесс в песочнице, поиск порта, прокси HTTP и WebSocket."""
from __future__ import annotations

import asyncio
import sys

from websockets.asyncio.client import connect

from layla_sandbox import config, server
from tests.test_sandbox import client, root_only, run, sync


async def _wait(http, owner, project, state="running", tries=120):
    body = {}
    for _ in range(tries):
        body = (await http.get(f"/workspaces/{owner}/{project}/service")).json()
        if body["state"] == state or body["state"] in ("exited", "no_port"):
            return body
        await asyncio.sleep(0.25)
    return body


@root_only
async def test_static_site_preview_is_served_and_stopped(work):
    async with client() as http:
        await sync(http, "alice", "site", {"index.html": "<html><head><title>Север</title></head><body><h1>Привет</h1></body></html>"})
        status = (await http.get("/workspaces/alice/site/service")).json()
        assert status == {"state": "none", "suggested": "python3 -m http.server $PORT --bind 127.0.0.1"}
        started = (await http.post("/workspaces/alice/site/service", json={})).json()
        assert started["state"] == "starting" and started["nonce"]
        body = await _wait(http, "alice", "site")
        assert body["state"] == "running" and body["port"], body
        page = await http.get("/workspaces/alice/site/service/http/index.html")
        assert page.status_code == 200 and "<h1>Привет</h1>" in page.text
        seen = (await http.get("/workspaces/alice/site/service/fetch", params={"path": "/"})).json()
        assert seen["status"] == 200 and seen["title"] == "Север" and "Привет" in seen["text"]
        # Обычная команда рядом с превью его не гасит.
        await run(http, "alice", "site", "echo hi")
        assert (await http.get("/workspaces/alice/site/service/http/")).status_code == 200
        assert (await http.delete("/workspaces/alice/site/service")).json() == {"stopped": True}
        assert (await http.get("/workspaces/alice/site/service/http/")).status_code == 404


@root_only
async def test_port_is_found_even_without_PORT_and_websocket_is_proxied(work):
    # Сервер websockets заодно строгий HTTP-сервер: GET с телом или Transfer-Encoding он отвергает.
    echo = (
        "import asyncio\n"
        "from http import HTTPStatus\n"
        "from websockets.asyncio.server import serve\n"
        "def page(connection, request):\n"
        "    if request.path.startswith('/plain'):\n"
        "        return connection.respond(HTTPStatus.OK, 'plain ok')\n"
        "async def echo(ws):\n"
        "    async for m in ws:\n"
        "        await ws.send('echo:' + m)\n"
        "async def main():\n"
        "    async with serve(echo, '127.0.0.1', 18765, process_request=page):\n"
        "        print('ready', flush=True)\n"
        "        await asyncio.Future()\n"
        "asyncio.run(main())\n"
    )
    async with client() as http:
        await sync(http, "bob", "ws", {"echo.py": echo})
        await http.post("/workspaces/bob/ws/service", json={"command": f"{sys.executable} echo.py"})
        body = await _wait(http, "bob", "ws")
        assert body["state"] == "running" and body["port"] == 18765, body
        plain = await http.get("/workspaces/bob/ws/service/http/plain")
        assert plain.status_code == 200 and plain.text.strip() == "plain ok"
    # WebSocket через настоящий сервер песочницы на TCP (ASGI-клиент httpx websockets не умеет).
    import uvicorn

    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=0, log_level="warning")
    srv = uvicorn.Server(cfg)
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    try:
        async with connect(f"ws://127.0.0.1:{port}/workspaces/bob/ws/service/ws/") as ws:
            await ws.send("привет")
            assert await ws.recv() == "echo:привет"
    finally:
        srv.should_exit = True
        await task
        await server.services.stop(server.users.uid("bob"), "ws")


@root_only
async def test_failed_command_reports_exit_and_logs(work):
    async with client() as http:
        await sync(http, "carol", "bad", {"a.txt": "a"})
        await http.post("/workspaces/carol/bad/service", json={"command": "echo boom; exit 3"})
        body = await _wait(http, "carol", "bad")
        assert body["state"] == "exited" and body["exit_code"] == 3
        logs = (await http.get("/workspaces/carol/bad/service", params={"logs": True})).json()["logs"]
        assert "boom" in logs
        missing = await http.post("/workspaces/carol/bad/service", json={})
        assert missing.status_code == 400 and "укажите команду" in missing.json()["detail"]


@root_only
async def test_idle_preview_is_stopped(work, monkeypatch):
    async with client() as http:
        await sync(http, "dan", "idle", {"index.html": "<p>x</p>"})
        await http.post("/workspaces/dan/idle/service", json={})
        assert (await _wait(http, "dan", "idle"))["state"] == "running"
        monkeypatch.setattr(config, "SERVICE_IDLE", 0)
        await server.services.reap()
        assert (await http.get("/workspaces/dan/idle/service")).json()["state"] == "idle"
        await server.services.stop(server.users.uid("dan"), "idle")
