"""Тесты генерации Design (спец. §5.6) с моком LiteLLM."""
from __future__ import annotations

import pytest

import app.services.provider_client as pc
from app.services import design_gen


def test_extract_html_from_fenced_block():
    text = "Вот результат:\n```html\n<h1>Привет</h1>\n```\nготово"
    assert design_gen.extract_html(text) == "<h1>Привет</h1>"


def test_extract_html_plain():
    assert design_gen.extract_html("<div>x</div>") == "<div>x</div>"


def test_build_prompt_contains_brief_fields():
    msgs = design_gen.build_prompt(
        {"artifact_type": "Dashboard", "direction": "Brutalist"}, "react"
    )
    assert msgs[0]["role"] == "system"
    assert "React" in msgs[0]["content"]
    assert "Dashboard" in msgs[1]["content"]
    assert "Brutalist" in msgs[1]["content"]


async def _register_with_active_provider(client):
    await client.post(
        "/api/auth/register",
        json={"email": "d@example.com", "password": "hunter2hunter2"},
    )
    await client.post(
        "/api/providers",
        json={
            "name": "M",
            "kind": "openai_compatible",
            "base_url": "http://prov.local/v1",
            "default_model": "gpt-4o",
            "active": True,
        },
    )


@pytest.mark.asyncio
async def test_generate_design(client, monkeypatch):
    await _register_with_active_provider(client)

    async def fake_complete(provider, key, model, messages, **kw):
        assert model == "gpt-4o"
        return "```html\n<!doctype html><title>Layla</title><h1>Demo</h1>\n```"

    monkeypatch.setattr(pc, "complete", fake_complete)

    r = await client.post(
        "/api/designs",
        json={"stack": "html", "brief": {"artifact_type": "Landing"}},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["files"][0]["name"] == "index.html"
    assert "<h1>Demo</h1>" in body["files"][0]["content"]

    # Появляется в списке.
    lst = (await client.get("/api/designs")).json()
    assert len(lst) == 1


@pytest.mark.asyncio
async def test_generate_without_model_rejected(client):
    await client.post(
        "/api/auth/register",
        json={"email": "d2@example.com", "password": "hunter2hunter2"},
    )
    r = await client.post("/api/designs", json={"stack": "html", "brief": {}})
    assert r.status_code == 400
