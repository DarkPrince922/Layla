"""Local provider fixture: finishes tasks without commands or external requests."""

import asyncio
import json
import sys
from pathlib import Path

import uvicorn
from fastapi import Request
from fastapi.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core"))
from app.db import Base, engine
from app.main import app


@app.post("/test-provider/chat/completions")
async def provider(request: Request):
    messages = (await request.json())["messages"]
    text = next(
        (
            m["content"]
            for m in reversed(messages)
            if m["role"] == "user" and m["content"].startswith("QA isolation")
        ),
        "QA isolation",
    )
    action = json.dumps({"action": "finish", "text": "QA isolated response: " + text})

    async def stream():
        yield (
            "data: "
            + json.dumps({"choices": [{"delta": {"content": action}, "finish_reason": None}]})
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def init():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(init())
    uvicorn.run(app, host="127.0.0.1", port=8219)
