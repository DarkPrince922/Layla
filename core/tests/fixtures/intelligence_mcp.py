"""A real MCP worker with only upstream HTTP replaced, for offline end-to-end tests."""

from __future__ import annotations

import httpx

from app.mcp_servers.intelligence import server
from app.services import intel_api

original = intel_api.lookup


def response(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET" and request.url.host == "urlscan.io"
    assert request.url.path == "/api/v1/search/"
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "_id": "8848d620-00b1-4c0a-a46a-83d369529487",
                    "page": {
                        "url": "https://example.com",
                        "title": "Example Domain",
                        "ip": "1.1.1.1",
                    },
                    "task": {"time": "2026-09-21T12:00:00Z"},
                }
            ]
        },
    )


async def lookup(provider, target, api_key=""):
    return await original(provider, target, api_key, transport=httpx.MockTransport(response))


intel_api.lookup = lookup
server.run(transport="stdio")
