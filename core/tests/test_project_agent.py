"""Exercise both providers' real SSE parsers through chat -> tools -> filesystem."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.services import files, project_agent, tool_chat


def call(name, arguments, ident="call1"):
    return {
        "id": ident,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def provider_response(native, calls=(), text="", finish=None, stopped=True):
    events = []
    if native:
        for index, item in enumerate(calls):
            events.append(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "tool_use",
                        "id": item["id"],
                        "name": item["function"]["name"],
                        "input": {},
                    },
                }
            )
        for offset in range(0, max((len(c["function"]["arguments"]) for c in calls), default=0), 7):
            for index, item in enumerate(calls):
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": item["function"]["arguments"][offset : offset + 7],
                        },
                    }
                )
        if text:
            events.append(
                {
                    "type": "content_block_delta",
                    "index": len(calls),
                    "delta": {"type": "text_delta", "text": text},
                }
            )
        events.append(
            {
                "type": "message_delta",
                "delta": {"stop_reason": finish or ("tool_use" if calls else "end_turn")},
            }
        )
        if stopped:
            events.append({"type": "message_stop"})
    else:
        for index, item in enumerate(calls):
            events.append(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": item["id"],
                                        "function": {
                                            "name": item["function"]["name"],
                                            "arguments": "",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            )
        for offset in range(0, max((len(c["function"]["arguments"]) for c in calls), default=0), 7):
            for index, item in enumerate(calls):
                events.append(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": index,
                                            "function": {
                                                "arguments": item["function"]["arguments"][
                                                    offset : offset + 7
                                                ]
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )
        if text:
            events.append({"choices": [{"delta": {"content": text}}]})
        events.append(
            {
                "choices": [
                    {"delta": {}, "finish_reason": finish or ("tool_calls" if calls else "stop")}
                ]
            }
        )
        events.append({"choices": [], "usage": {"total_tokens": 100}})
    wire = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
    if not native and stopped:
        wire += "data: [DONE]\n\n"
    return httpx.Response(200, text=wire, headers={"content-type": "text/event-stream"})


def mock_provider(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        tool_chat.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )


def results(payload, native):
    if native:
        return [
            json.loads(b["content"])
            for m in payload["messages"]
            for b in m["content"]
            if b["type"] == "tool_result"
        ]
    return [json.loads(m["content"]) for m in payload["messages"] if m["role"] == "tool"]


@pytest.fixture
async def project_chat(client, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "projects_dir", str(tmp_path))
    await client.post(
        "/api/auth/register", json={"email": "agentfiles@example.com", "password": "hunter2hunter2"}
    )
    project = (await client.post("/api/projects", json={"name": "Build"})).json()
    chat = (
        await client.post("/api/chats", json={"project_id": project["id"], "model": "test-model"})
    ).json()
    return project, chat


async def add_provider(client, native=False):
    response = await client.post(
        "/api/providers",
        json={
            "name": "Test",
            "kind": "anthropic" if native else "openai_compatible",
            "active": True,
            "default_model": "test-model",
        },
    )
    assert response.status_code == 201


@pytest.mark.parametrize("native", [False, True])
async def test_agent_creates_reads_edits_deletes_and_persists(
    client, project_chat, monkeypatch, native
):
    project, chat = project_chat
    await add_provider(client, native)
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        round_number = len(requests) - 1
        assert len(body["tools"]) == 4
        history = results(body, native)
        if round_number == 0:
            return provider_response(native, [call("list_files", {"path": "."})])
        if round_number == 1:
            assert history[-1] == {"files": []}
            return provider_response(
                native,
                [
                    call(
                        "write_file",
                        {
                            "path": "src/main.py",
                            "content": "print('hello')\n",
                            "expected_sha256": None,
                        },
                        "main",
                    ),
                    call(
                        "write_file",
                        {"path": "temp.txt", "content": "temporary", "expected_sha256": None},
                        "temp",
                    ),
                ],
            )
        if round_number == 2:
            assert history[-1]["change"]["path"] == "temp.txt"
            return provider_response(native, [call("read_file", {"path": "src/main.py"})])
        if round_number == 3:
            read = history[-1]
            assert read["content"] == "print('hello')\n"
            return provider_response(
                native,
                [
                    call(
                        "write_file",
                        {
                            "path": "src/main.py",
                            "content": "print('world')\n",
                            "expected_sha256": read["sha256"],
                        },
                        "edit",
                    ),
                    call(
                        "delete_file",
                        {
                            "path": "temp.txt",
                            "expected_sha256": history[-2]["change"]["after_sha256"],
                        },
                        "delete",
                    ),
                ],
            )
        return provider_response(native, text="Готово. Файлы созданы.")

    mock_provider(monkeypatch, handler)
    response = await client.post(
        f"/api/chats/{chat['id']}/messages",
        json={"content": "Создай проект и убери временный файл"},
    )
    assert response.status_code == 200 and '"done": true' in response.text
    assert '"error":' not in response.text, response.text
    root = Path(project["path"])
    assert (root / "src/main.py").read_text() == "print('world')\n"
    assert not (root / "temp.txt").exists()
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert detail["project_id"] == project["id"]
    reply = detail["messages"][-1]
    assert reply["content"] == "Готово. Файлы созданы."
    changes = [t["change"] for t in reply["meta"]["tools"] if t.get("change")]
    assert [c["operation"] for c in changes] == ["create", "create", "edit", "delete"]
    assert "-print('hello')" in changes[2]["diff"]
    assert all(t["status"] == "done" for t in reply["meta"]["tools"])
    assert len((await client.get("/api/chats", params={"project_id": project["id"]})).json()) == 1
    # A later user turn reloads persisted history and can continue the same project.
    await client.post(
        f"/api/chats/{chat['id']}/messages", json={"content": "Какие файлы изменены?"}
    )
    assert "Applied project changes" in json.dumps(requests[-1])


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "failure", ["truncated_json", "no_stop", "token_limit", "bad_arguments", "http_error"]
)
async def test_incomplete_calls_never_write(client, project_chat, monkeypatch, native, failure):
    project, chat = project_chat
    await add_provider(client, native)

    def handler(request):
        item = call(
            "write_file", {"path": "bad", "content": "not written", "expected_sha256": None}
        )
        if failure == "truncated_json":
            item["function"]["arguments"] = '{"path":"bad"'
        if failure == "bad_arguments":
            item["function"]["arguments"] = "[]"
        if failure == "http_error":
            return httpx.Response(429, text="secret-key-do-not-leak")
        return provider_response(
            native,
            [item],
            stopped=failure != "no_stop",
            finish=("max_tokens" if native else "length") if failure == "token_limit" else None,
        )

    mock_provider(monkeypatch, handler)
    response = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "go"})
    assert '"error":' in response.text and "secret-key-do-not-leak" not in response.text
    assert not list(Path(project["path"]).iterdir())
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert detail["messages"][-1]["meta"]["error"]


async def test_tool_errors_are_returned_and_partial_diffs_survive_provider_failure(
    client, project_chat, monkeypatch
):
    project, chat = project_chat
    await add_provider(client)
    step = 0

    def handler(request):
        nonlocal step
        step += 1
        body = json.loads(request.content)
        if step == 1:
            return provider_response(
                False,
                [
                    call(
                        "write_file",
                        {"path": "../escape", "content": "bad", "expected_sha256": None},
                    )
                ],
            )
        if step == 2:
            assert "error" in results(body, False)[-1]
            return provider_response(
                False,
                [call("write_file", {"path": "ok", "content": "saved", "expected_sha256": None})],
            )
        return httpx.Response(503)

    mock_provider(monkeypatch, handler)
    response = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "go"})
    assert '"error":' in response.text
    assert (Path(project["path"]) / "ok").read_text() == "saved"
    assert not (Path(project["path"]).parent / "escape").exists()
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    tools = detail["messages"][-1]["meta"]["tools"]
    assert tools[0]["status"] == "error" and tools[1]["change"]["operation"] == "create"


async def test_chat_project_binding_and_ownership(client, project_chat):
    project, chat = project_chat
    assert (
        await client.post("/api/chats", json={"project_id": project["id"], "domain": "osint"})
    ).status_code == 201
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/register", json={"email": "otheragent@example.com", "password": "hunter2hunter2"}
    )
    assert (await client.post("/api/chats", json={"project_id": project["id"]})).status_code == 404
    assert (await client.get("/api/chats", params={"project_id": project["id"]})).status_code == 404
    assert (
        await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "go"})
    ).status_code == 404


async def test_call_budget_does_not_execute_excess_batch(client, project_chat, monkeypatch):
    project, chat = project_chat
    await add_provider(client)
    monkeypatch.setattr(project_agent, "MAX_CALLS", 1)
    mock_provider(
        monkeypatch,
        lambda _: provider_response(
            False,
            [
                call("write_file", {"path": "a", "content": "a", "expected_sha256": None}, "a"),
                call("write_file", {"path": "b", "content": "b", "expected_sha256": None}, "b"),
            ],
        ),
    )
    response = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "go"})
    assert "лимит" in response.text
    assert files.list_dir(project["path"]) == []


def test_tool_arguments_cannot_override_root(tmp_path):
    for name, args in [
        ("shell", {"command": "echo nope"}),
        ("write_file", {"path": "a", "content": "a", "expected_sha256": None, "root": "/tmp"}),
    ]:
        assert "error" in project_agent.execute(str(tmp_path), name, args)
    assert not list(tmp_path.iterdir())


async def test_readonly_persona_cannot_write_even_if_model_requests_it(
    client, project_chat, monkeypatch
):
    project, _ = project_chat
    await add_provider(client)
    personas = (await client.get("/api/personas")).json()
    reviewer = next(p for p in personas if p["kind"] == "review")
    chat = (
        await client.post(
            "/api/chats",
            json={"project_id": project["id"], "model": "test-model", "persona_id": reviewer["id"]},
        )
    ).json()
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        body = json.loads(request.content)
        assert {t["function"]["name"] for t in body["tools"]} == {"list_files", "read_file"}
        if count == 1:
            return provider_response(
                False,
                [
                    call(
                        "write_file",
                        {"path": "forbidden", "content": "no", "expected_sha256": None},
                    )
                ],
            )
        assert "error" in results(body, False)[-1]
        return provider_response(False, text="Только чтение.")

    mock_provider(monkeypatch, handler)
    await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "Измени файл"})
    assert not list(Path(project["path"]).iterdir())


async def test_round_limit_keeps_completed_changes(client, project_chat, monkeypatch):
    project, chat = project_chat
    await add_provider(client)
    monkeypatch.setattr(project_agent, "MAX_ROUNDS", 1)
    mock_provider(
        monkeypatch,
        lambda _: provider_response(
            False, [call("write_file", {"path": "saved", "content": "ok", "expected_sha256": None})]
        ),
    )
    response = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "go"})
    assert "лимит" in response.text and (Path(project["path"]) / "saved").read_text() == "ok"
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert detail["messages"][-1]["meta"]["tools"][0]["change"]["path"] == "saved"


@pytest.mark.parametrize("domain", ["code", "osint", "design", "pentest"])
async def test_background_tools_every_domain(client, monkeypatch, tmp_path, domain):
    import asyncio
    import io
    import zipfile
    monkeypatch.setattr(get_settings(), "projects_dir", str(tmp_path))
    await client.post("/api/auth/register", json={"email": "domains@example.com", "password": "hunter2hunter2"})
    await add_provider(client)
    personas = (await client.get("/api/personas")).json()
    persona = next((p["id"] for p in personas if p["kind"] == domain), None)
    chat = (await client.post("/api/chats", json={"domain": domain, "model": "test-model", "persona_id": persona})).json()
    turns = []
    def handler(request):
        body = json.loads(request.content)
        turns.append(body)
        if len(turns) == 1:
            response = provider_response(False, [call("write_file", {"path": "report.md", "content": "# Saved"})])
            # Thinking providers require this field to be replayed along with tool calls.
            prefix = 'data: ' + json.dumps({"choices": [{"delta": {"reasoning_content": "Plan a report"}}]}) + '\n\n'
            # Some compatible gateways repeat the complete tool name/id on later chunks.
            wire = response.text
            if domain == "code":
                first = wire.split("\n\n", 1)[0] + "\n\n"
                wire = first + wire
            return httpx.Response(200, text=prefix + wire)
        assistant = next(m for m in reversed(body["messages"]) if m["role"] == "assistant")
        assert assistant["reasoning_content"] == "Plan a report"
        assert results(body, False)[-1]["change"]["operation"] == "create"
        return provider_response(False, text="Report saved")
    mock_provider(monkeypatch, handler)
    response = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Create report", "request_id": "once"})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    for _ in range(150):
        state = (await client.get(f"/api/jobs/{job_id}")).json()
        if state["status"] in ("done", "error"): break
        await asyncio.sleep(.02)
    assert state["status"] == "done", state
    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert detail["last_job"]["id"] == job_id
    assert detail["messages"][-1]["meta"]["tools"][0]["change"]["path"] == "report.md"
    archive = await client.get(f"/api/projects/{detail['project_id']}/archive")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.read("report.md") == b"# Saved"
    # Retrying a lost HTTP response must never execute the same turn twice.
    retry = await client.post(f"/api/chats/{chat['id']}/run", json={"content": "Create report", "request_id": "once"})
    assert retry.json()["id"] == job_id
    assert len((await client.get(f"/api/chats/{chat['id']}")).json()["messages"]) == 2
    assert len(turns) == 2


async def test_native_proxy_replays_signed_thinking(client, project_chat, monkeypatch):
    project, chat = project_chat
    response = await client.post('/api/providers', json={
        'name': 'Native proxy', 'kind': 'anthropic', 'active': True,
        'base_url': 'https://proxy.example/v1', 'default_model': 'test-model',
    })
    assert response.status_code == 201
    turns = []
    def handler(request):
        assert str(request.url) == 'https://proxy.example/v1/messages'
        body = json.loads(request.content)
        turns.append(body)
        if len(turns) == 1:
            thinking = [
                {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}},
                {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'Need a file'}},
                {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': 'signed-proof'}},
            ]
            prefix = ''.join('data: ' + json.dumps(e) + '\n\n' for e in thinking)
            wire = provider_response(True, [call('write_file', {'path': 'native.md', 'content': 'saved'})]).text.replace('"index": 0', '"index": 1')
            return httpx.Response(200, text=prefix + wire)
        assistant = next(m for m in body['messages'] if m['role'] == 'assistant')
        assert assistant['content'][0] == {'type': 'thinking', 'thinking': 'Need a file', 'signature': 'signed-proof'}
        assert results(body, True)[-1]['change']['operation'] == 'create'
        return provider_response(True, text='Saved through native proxy')
    mock_provider(monkeypatch, handler)
    result = await client.post(f"/api/chats/{chat['id']}/messages", json={'content': 'create'})
    assert 'Saved through native proxy' in result.text
    assert (Path(project['path']) / 'native.md').read_text() == 'saved'
