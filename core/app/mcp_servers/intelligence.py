"""Passive intelligence MCP server, launched over stdio by Layla Core.

Credentials belong to one user/provider for the lifetime of this subprocess.
They are passed in its environment, never tool arguments or command lines.
Only four read-only tools exist; no arbitrary URL, scan or shell tool.
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.schemas.intelligence import IntelResult
from app.services import intel_api

server = FastMCP("Layla passive intelligence", log_level="WARNING")
_annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


async def _lookup(provider: str, target: str) -> IntelResult:
    # Shodan authenticates in a query string. Never log request URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if os.environ.get("LAYLA_INTEL_PROVIDER") != provider:
        return intel_api.failure("not_configured")
    return await intel_api.lookup(provider, target, os.environ.get("LAYLA_INTEL_API_KEY", ""))


@server.tool(annotations=_annotations)
async def shodan_lookup(target: str) -> IntelResult:
    """Read existing Shodan host/DNS data for a public IP or domain. No scans."""
    return await _lookup("shodan", target)


@server.tool(annotations=_annotations)
async def virustotal_lookup(target: str) -> IntelResult:
    """Read an existing VirusTotal domain or IP report. No submissions."""
    return await _lookup("virustotal", target)


@server.tool(annotations=_annotations)
async def securitytrails_lookup(target: str) -> IntelResult:
    """Read SecurityTrails domain and DNS data. No direct target requests."""
    return await _lookup("securitytrails", target)


@server.tool(annotations=_annotations)
async def urlscan_lookup(target: str) -> IntelResult:
    """Search existing urlscan results for a domain/IP. Never submit a scan."""
    return await _lookup("urlscan", target)


if __name__ == "__main__":
    server.run(transport="stdio")
