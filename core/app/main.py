"""Layla Core — FastAPI application entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    auth,
    chats,
    designs,
    engagements,
    findings,
    health,
    intelligence,
    knowledge,
    mcp,
    models,
    osint,
    personas,
    projects,
    providers,
    servers,
    telegram,
)
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

settings = get_settings()

app = FastAPI(
    title="Layla Core",
    version="0.1.0",
    description="Backend orchestrating Layla's Code / Pentest / OSINT / Design domains.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request, exc: RequestValidationError):
    # Pydantic's default error includes the original input (possibly an API key).
    return JSONResponse(
        status_code=422,
        content={"detail": [
            {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
        ]},
    )


# In prod the frontend and API share an origin behind Caddy, so CORS is only
# needed for local split-origin dev.
if not settings.is_prod:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# All routes are served under /api (Caddy routes /api/* to core).
api_prefix = "/api"
app.include_router(health.router, prefix=api_prefix)
app.include_router(auth.router, prefix=api_prefix)
app.include_router(providers.router, prefix=api_prefix)
app.include_router(personas.router, prefix=api_prefix)
app.include_router(models.router, prefix=api_prefix)
app.include_router(chats.router, prefix=api_prefix)
app.include_router(projects.router, prefix=api_prefix)
app.include_router(designs.router, prefix=api_prefix)
app.include_router(knowledge.router, prefix=api_prefix)
app.include_router(mcp.router, prefix=api_prefix)
app.include_router(telegram.router, prefix=api_prefix)
app.include_router(intelligence.router, prefix=api_prefix)
app.include_router(osint.router, prefix=api_prefix)
app.include_router(engagements.router, prefix=api_prefix)
app.include_router(servers.router, prefix=api_prefix)
app.include_router(findings.router, prefix=api_prefix)


@app.get("/")
async def root() -> dict:
    return {"service": "layla-core", "docs": "/api/docs"}
