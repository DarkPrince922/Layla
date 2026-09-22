"""Layla Core — FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    admin,
    agent,
    audit,
    auth,
    chats,
    combos,
    designs,
    engagements,
    findings,
    health,
    intelligence,
    jobs,
    knowledge,
    mcp,
    models,
    osint,
    pentest_imports,
    personas,
    projects,
    providers,
    servers,
    telegram,
)
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

settings = get_settings()

# Прод-hardening: не стартуем с небезопасными дефолтами (спец. §7).
_prod_problems = settings.validate_for_prod()
if _prod_problems:
    raise RuntimeError("Небезопасная конфигурация prod: " + "; ".join(_prod_problems))

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Первый запуск: гарантировать администратора (создать со случайным паролем
    # или повысить существующего). Не валим старт, если БД ещё недоступна.
    from app.db import SessionLocal
    from app.services.auth import ensure_admin_bootstrapped
    from app.services.jobs import reap_stale

    try:
        async with SessionLocal() as session:
            await ensure_admin_bootstrapped(session)
        # Задачи, зависшие в running после прошлого запуска, помечаем прерванными.
        await reap_stale(SessionLocal)
    except Exception:  # noqa: BLE001 — старт не должен падать из-за бутстрапа
        logging.getLogger("layla").exception("Бутстрап/очистка задач не выполнены")
    yield


app = FastAPI(
    title="Layla Core",
    version="0.1.0",
    description="Backend orchestrating Layla's Code / Pentest / OSINT / Design domains.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
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
app.include_router(admin.router, prefix=api_prefix)
app.include_router(providers.router, prefix=api_prefix)
app.include_router(personas.router, prefix=api_prefix)
app.include_router(models.router, prefix=api_prefix)
app.include_router(chats.router, prefix=api_prefix)
app.include_router(projects.router, prefix=api_prefix)
app.include_router(designs.router, prefix=api_prefix)
app.include_router(jobs.router, prefix=api_prefix)
app.include_router(knowledge.router, prefix=api_prefix)
app.include_router(mcp.router, prefix=api_prefix)
app.include_router(telegram.router, prefix=api_prefix)
app.include_router(intelligence.router, prefix=api_prefix)
app.include_router(osint.router, prefix=api_prefix)
app.include_router(engagements.router, prefix=api_prefix)
app.include_router(servers.router, prefix=api_prefix)
app.include_router(findings.router, prefix=api_prefix)
app.include_router(pentest_imports.router, prefix=api_prefix)
app.include_router(agent.router, prefix=api_prefix)
app.include_router(combos.router, prefix=api_prefix)
app.include_router(audit.router, prefix=api_prefix)


@app.get("/")
async def root() -> dict:
    return {"service": "layla-core", "docs": "/api/docs"}
