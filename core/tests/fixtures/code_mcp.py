"""Real stateful stdio server used only by Code lifecycle tests."""
from mcp.server.fastmcp import FastMCP

server = FastMCP("Code fixture")
count = 0


@server.tool()
def increment() -> dict:
    global count
    count += 1
    return {"count": count}


@server.resource("test://binary", mime_type="application/octet-stream")
def binary() -> bytes:
    return b"Code MCP binary result"


if __name__ == "__main__":
    server.run(transport="stdio")
