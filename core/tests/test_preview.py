"""Превью приложения: токен и вход, шлюз на отдельном адресе, защита API, инструменты агента."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from websockets.asyncio.server import unix_serve

from app.config import get_settings
from app.main import app
from app.services import code_runner, preview, project_agent, sandbox

pytestmark = pytest.mark.asyncio

MARK = {"x-layla-preview": "1"}


class _Body(httpx.AsyncByteStream):
    """Потоковое тело, как у настоящей песочницы (Response(content=...) httpx читает заранее)."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self):
        for i in range(0, len(self.data), 16):
            yield self.data[i:i + 16]


def _reply(status: int, headers: dict, data: bytes) -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=_Body(data))


def _gateway() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://host.test:8090")


@pytest.fixture
def live(monkeypatch):
    """Превью «запущено»: песочница отвечает running с этим nonce, запросы к ней записываются."""
    seen: list[httpx.Request] = []
    state = {"nonce": "n1", "state": "running", "reply": None}

    async def status(owner_id, project_id, **kw):
        return {"state": state["state"], "nonce": state["nonce"]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if state["reply"] is not None:
            return state["reply"](request)
        return _reply(200, {"content-type": "text/html; charset=utf-8", "set-cookie": "layla_session=evil; Path=/"},
                      b"<html><head><title>App</title></head><body>hi</body></html>")

    monkeypatch.setattr(sandbox, "preview_status", status)
    monkeypatch.setattr(preview, "_status", {})
    monkeypatch.setattr(preview, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(get_settings(), "sandbox_url", "http://sandbox.test")
    return state, seen


async def test_token_is_signed_and_bound_to_the_run():
    token = preview.make_token("u1", "p1", "n1")
    assert preview.parse_token(token) == ("u1", "p1", "n1")
    project, owner, nonce, sig = token.split(".")
    assert preview.parse_token(f"{project}.u2.{nonce}.{sig}") is None  # чужой владелец
    assert preview.parse_token(f"{project}.{owner}.n2.{sig}") is None  # другой запуск
    assert preview.parse_token("garbage") is None and preview.parse_token(None) is None


async def test_preview_address_follows_the_host(monkeypatch):
    monkeypatch.setattr(get_settings(), "preview_url", "")
    monkeypatch.setattr(get_settings(), "preview_port", 8090)
    assert preview.base_url("https", "layla.example:443") == "https://layla.example:8090"
    assert preview.base_url("http", "10.0.0.5") == "http://10.0.0.5:8090"
    assert preview.base_url("http", "[::1]:80") == "http://[::1]:8090"
    monkeypatch.setattr(get_settings(), "preview_url", "https://preview.example/")
    assert preview.base_url("https", "layla.example") == "https://preview.example"


async def test_open_link_sets_cookie_and_rejects_bad_tokens(live):
    token = preview.make_token("u1", "p1", "n1")
    async with _gateway() as http:
        r = await http.get("/__layla__/open", params={"t": token, "path": "/about"}, headers=MARK)
        assert r.status_code == 302 and r.headers["location"] == "/about"
        cookie = r.headers["set-cookie"]
        assert cookie.startswith(f"layla_preview={token};") and "HttpOnly" in cookie and "Secure" not in cookie
        r = await http.get("/__layla__/open", params={"t": token, "path": "//evil.example"},
                           headers={**MARK, "x-forwarded-proto": "https"})
        assert r.headers["location"] == "/" and "Secure" in r.headers["set-cookie"]  # только свой путь
        r = await http.get("/__layla__/open", params={"t": token[:-2] + "xx"}, headers=MARK)
        assert r.status_code == 403


async def test_requests_without_cookie_or_after_restart_get_a_hint(live):
    _, seen = live
    async with _gateway() as http:
        r = await http.get("/", headers=MARK)
        assert r.status_code == 503 and "Превью не запущено" in r.text
        http.cookies.set("layla_preview", preview.make_token("u1", "p1", "old"))
        assert (await http.get("/", headers=MARK)).status_code == 503  # ссылка прошлого запуска
    assert seen == []


async def test_gateway_forwards_to_the_app_and_injects_the_picker(live):
    _, seen = live
    token = preview.make_token("u1", "p1", "n1")
    async with _gateway() as http:
        r = await http.get("/page?x=1", headers={**MARK, "cookie": f"layla_preview={token}; layla_session=secret; "
                                                                    "theme=dark"})
    assert r.status_code == 200
    assert b'<script src="/__layla__/pick.js"></script></head>' in r.content
    assert "set-cookie" not in r.headers  # приложение не подменит куку сессии Лейлы
    sent = seen[0]
    assert str(sent.url) == "http://sandbox.test/workspaces/u1/p1/service/http/page?x=1"
    assert sent.headers["cookie"] == "theme=dark"  # куки Лейлы до приложения не доходят
    assert "x-layla-preview" not in sent.headers
    # GET без тела уходит без тела: строгие серверы отвергают Transfer-Encoding у GET.
    assert "transfer-encoding" not in sent.headers and "content-length" not in sent.headers


async def test_gateway_streams_other_content_as_is(live):
    state, seen = live
    state["reply"] = lambda request: _reply(
        201, {"content-type": "application/json", "set-cookie": "sid=1; Path=/"},
        json.dumps({"method": request.method, "body": request.content.decode()}).encode())
    token = preview.make_token("u1", "p1", "n1")
    async with _gateway() as http:
        http.cookies.set("layla_preview", token)
        r = await http.post("/api/items", headers=MARK, content=b'{"a": 1}')
    assert r.status_code == 201 and r.json() == {"method": "POST", "body": '{"a": 1}'}
    assert r.headers["set-cookie"] == "sid=1; Path=/"  # свои куки приложения проходят
    sent = seen[0]
    assert sent.headers["content-length"] == "8" and "transfer-encoding" not in sent.headers


async def test_picker_script_is_served_and_matches_the_frontend(live):
    async with _gateway() as http:
        r = await http.get("/__layla__/pick.js", headers=MARK)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    source = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "pick.ts"
    if not source.exists():
        pytest.skip("нет исходников фронтенда")
    script = re.search(r"export const PICK_SCRIPT = `(.*?)`;\n", source.read_text(), re.DOTALL).group(1)
    assert "${" not in script and "\\" not in script
    assert r.text.rstrip("\n") == script, "core/app/static/pick.js должен совпадать с PICK_SCRIPT из pick.ts"


async def test_without_the_mark_requests_reach_layla(live):
    _, seen = live
    async with _gateway() as http:
        r = await http.get("/api/health")
    assert r.status_code == 200 and seen == []


async def test_websocket_is_proxied_to_the_sandbox(live, monkeypatch, tmp_path):
    socket_path = str(tmp_path / "sb.sock")
    paths = []

    async def echo(ws):
        paths.append(ws.request.path)
        async for message in ws:
            await ws.send(f"echo:{message}" if isinstance(message, str) else message)

    monkeypatch.setattr(get_settings(), "sandbox_url", f"unix://{socket_path}")
    token = preview.make_token("u1", "p1", "n1")
    scope = {"type": "websocket", "path": "/_next/hmr", "query_string": b"v=2", "subprotocols": [],
             "headers": [(b"x-layla-preview", b"1"), (b"cookie", f"layla_preview={token}".encode())]}
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    for message in ({"type": "websocket.connect"}, {"type": "websocket.receive", "text": "ping"},
                    {"type": "websocket.receive", "bytes": b"\x00\x01"}):
        inbox.put_nowait(message)
    async with unix_serve(echo, socket_path):
        task = asyncio.create_task(preview.PreviewGateway(app)(scope, inbox.get, outbox.put))
        got = [await asyncio.wait_for(outbox.get(), 5) for _ in range(3)]
        inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 5)
    assert got[0]["type"] == "websocket.accept"
    assert got[1] == {"type": "websocket.send", "text": "echo:ping"}
    assert got[2] == {"type": "websocket.send", "bytes": b"\x00\x01"}
    assert paths == ["/workspaces/u1/p1/service/ws/_next/hmr?v=2"]


async def test_websocket_without_a_valid_cookie_is_closed(live):
    outbox: asyncio.Queue = asyncio.Queue()
    inbox: asyncio.Queue = asyncio.Queue()
    inbox.put_nowait({"type": "websocket.connect"})
    scope = {"type": "websocket", "path": "/", "headers": [(b"x-layla-preview", b"1")]}
    await preview.PreviewGateway(app)(scope, inbox.get, outbox.put)
    assert (await outbox.get()) == {"type": "websocket.close", "code": 1008}


async def test_api_rejects_changes_from_foreign_pages(client):
    body = {"email": "o@example.com", "password": "hunter2hunter2"}
    r = await client.post("/api/auth/register", json=body, headers={"origin": "http://test:8090"})
    assert r.status_code == 403  # страница превью — другой origin
    r = await client.post("/api/auth/register", json=body, headers={"origin": "null"})
    assert r.status_code == 403
    r = await client.post("/api/auth/register", json=body, headers={"origin": "http://test"})
    assert r.status_code == 201  # сама Лейла
    r = await client.post("/api/auth/login", json=body,
                          headers={"origin": "https://layla.example", "x-forwarded-host": "layla.example"})
    assert r.status_code == 200  # за Caddy: исходный хост — в X-Forwarded-Host
    assert (await client.get("/api/auth/me", headers={"origin": "http://evil.example"})).status_code == 200


def _fake_service(monkeypatch, calls: list, *, state="running"):
    async def call(method, url, *, json_body=None, params=None, timeout=60):
        calls.append((method, url, json_body, params))
        if url.endswith("/fetch"):
            return {"state": "running", "status": 200, "title": "Мой сайт", "text": "Привет, мир", "logs_tail": ""}
        if method == "DELETE":
            return {"state": "stopped"}
        return {"state": state, "nonce": "n7", "port": 5173, "command": "npm run dev", "logs": "ready in 300ms\n"}

    async def sync(owner_id, project_id, root):
        calls.append(("SYNC", owner_id, project_id, root))

    monkeypatch.setattr(sandbox, "_service_call", call)
    monkeypatch.setattr(sandbox, "sync", sync)
    monkeypatch.setattr(sandbox, "_previews", {})


async def test_preview_api_gives_a_signed_link(client, monkeypatch):
    calls: list = []
    _fake_service(monkeypatch, calls)
    monkeypatch.setattr(get_settings(), "preview_url", "")
    await client.post("/api/auth/register", json={"email": "pv@example.com", "password": "hunter2hunter2"})
    project = (await client.post("/api/projects", json={"name": "Сайт"})).json()
    r = await client.post(f"/api/projects/{project['id']}/preview", json={"command": "npm run dev"},
                          headers={"host": "layla.example"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["state"] == "running" and data["base"] == "http://layla.example:8090" and "nonce" not in data
    token = unquote(re.search(r"t=([^&]+)", data["url"]).group(1))
    me = (await client.get("/api/auth/me")).json()
    assert preview.parse_token(token) == (me["id"], project["id"], "n7")
    assert calls[0][0] == "SYNC" and calls[1][2] == {"command": "npm run dev"}
    state = (await client.get(f"/api/projects/{project['id']}/preview")).json()
    assert state["url"] and calls[-1][3] == {"logs": True}
    assert (await client.delete(f"/api/projects/{project['id']}/preview")).json() == {"state": "stopped"}
    other = await client.get("/api/projects/nope/preview")
    assert other.status_code == 404


async def test_preview_follows_file_changes(monkeypatch, tmp_path):
    calls: list = []
    _fake_service(monkeypatch, calls)
    sandbox.preview_touch("u1", "p1")  # превью не запущено — ничего
    await sandbox.preview_start("u1", "p1", str(tmp_path))
    calls.clear()
    for _ in range(3):  # пачка правок — одна синхронизация
        sandbox.preview_touch("u1", "p1")
    await asyncio.sleep(1.2)
    assert calls == [("SYNC", "u1", "p1", str(tmp_path))]
    await sandbox.preview_stop("u1", "p1")
    calls.clear()
    sandbox.preview_touch("u1", "p1")
    await asyncio.sleep(1.0)
    assert calls == []
    # Ядро перезапустилось, а превью в песочнице живо: опрос состояния снова включает синхронизацию.
    await sandbox.preview_status("u1", "p1", root=str(tmp_path))
    sandbox.preview_touch("u1", "p1")
    await asyncio.sleep(1.2)
    assert calls[-1] == ("SYNC", "u1", "p1", str(tmp_path))


async def test_agent_starts_and_checks_the_preview(monkeypatch, tmp_path):
    calls: list = []
    _fake_service(monkeypatch, calls)

    async def info(**kw):
        return {"tools": {"node": "v22"}, "network": "proxy"}

    monkeypatch.setattr(sandbox, "info", info)
    monkeypatch.setattr(sandbox, "runtimes", lambda **kw: _none())
    seen: list = []
    turns = [[{"name": "start_preview", "args": {"command": "npm run dev"}}],
             [{"name": "check_preview", "args": {"path": "/about"}}], []]

    async def stream_turn(provider, key, model, conversation, available, **kw):
        seen.append({"tools": {t["function"]["name"] for t in available}, "conversation": list(conversation)})
        batch = turns[len(seen) - 1] if len(seen) <= len(turns) else []
        if batch:
            yield ("tool_calls", [{"id": f"c{i}", "type": "function",
                                   "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                  for i, c in enumerate(batch)])
        else:
            yield ("content", "Готово")

    monkeypatch.setattr(project_agent.tool_chat, "stream_turn", stream_turn)
    runner = code_runner.Runner("u1", "p1", str(tmp_path))
    events = [e async for e in project_agent.run(None, "k", "m", [{"role": "user", "content": "запусти"}],
                                                   str(tmp_path), runner=runner)]
    assert {"start_preview", "check_preview", "stop_preview"} <= seen[0]["tools"]
    cards = [e["tool"] for e in events if "tool" in e and e["tool"]["status"] == "done"]
    assert cards[0]["name"] == "start_preview" and "порт 5173" in cards[0]["output"]
    started = json.loads(seen[1]["conversation"][-1]["content"])
    assert started["state"] == "running" and started["first_page"]["title"] == "Мой сайт"
    checked = json.loads(seen[2]["conversation"][-1]["content"])
    assert checked["text"] == "Привет, мир"
    assert ("GET", "/workspaces/u1/p1/service/fetch", None, {"path": "/about"}) in calls


async def _none():
    return None
