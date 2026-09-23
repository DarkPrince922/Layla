"""Запуск кода из интерфейса: состояние песочницы и Piston, языки, терминал проекта."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.projects import _owned_project, _project_root
from app.db import get_session, get_sessionmaker
from app.models.job import Job
from app.models.user import User
from app.schemas.job import JobOut
from app.services import audit, code_runner, jobs, sandbox
from app.services.auth import get_current_user, require_admin

router = APIRouter(tags=["sandbox"])


@router.get("/sandbox/status")
async def sandbox_status(user: User = Depends(get_current_user)) -> dict:
    box = await sandbox.info(fresh=True)
    runtimes = await sandbox.runtimes(fresh=True)
    return {
        "sandbox": {"available": box is not None, **(box or {})},
        "piston": {"available": runtimes is not None, "runtimes": runtimes or []},
    }


@router.get("/sandbox/languages")
async def piston_languages(user: User = Depends(get_current_user)) -> list[dict]:
    """Все языки из репозитория Piston и какие из них установлены."""
    try:
        return await sandbox.packages()
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class LanguageIn(BaseModel):
    language: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_.+#-]+$")
    version: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.x*+-]+$")


@router.post("/sandbox/languages", response_model=JobOut, status_code=202)
async def install_language(
    body: LanguageIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    maker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> Job:
    """Установить язык в Piston (общий для всех пользователей — только админ). Идёт в фоне."""
    job = await jobs.create_job(session, owner_id=user.id, domain="code", kind="sandbox.install",
                                title=f"Установка языка {body.language} {body.version}")
    await audit.record(session, actor=user.id, action="sandbox.language.install",
                       target=f"{body.language}={body.version}")
    await session.commit()

    async def worker(h: jobs.JobHandle) -> None:
        await h.step(f"Скачиваю и устанавливаю {body.language} {body.version} в Piston", progress=0.1)
        try:
            done = await sandbox.change_package(body.language, body.version, install=True)
        except sandbox.SandboxError as exc:
            raise RuntimeError(str(exc)) from exc
        await h.set_result({"language": done.get("language"), "version": done.get("version")})
        await h.step(f"Установлено: {done.get('language')} {done.get('version')}", progress=1.0)

    jobs.launch(maker, job.id, worker)
    return job


@router.delete("/sandbox/languages")
async def remove_language(
    body: LanguageIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        done = await sandbox.change_package(body.language, body.version, install=False)
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await audit.record(session, actor=user.id, action="sandbox.language.remove",
                       target=f"{body.language}={body.version}")
    await session.commit()
    return done


class RunIn(BaseModel):
    command: str = Field(min_length=1, max_length=8000)
    timeout: int = Field(default=sandbox.DEFAULT_TIMEOUT, ge=1, le=sandbox.MAX_TIMEOUT)
    stdin: str | None = Field(default=None, max_length=100_000)


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


@router.post("/projects/{project_id}/run")
async def run_in_project(
    project_id: str,
    body: RunIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Терминал проекта: команда в песочнице, вывод — потоком (SSE)."""
    project = await _owned_project(session, user, project_id)
    root = _project_root(project)
    await audit.record(session, actor=user.id, action="project.run", target=project_id,
                       meta={"command": body.command[:500]})
    await session.commit()

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for event in sandbox.run(user.id, project_id, root, body.command,
                                           timeout=body.timeout, stdin=body.stdin):
                if event.get("type") == "output":
                    event = {**event, "data": sandbox.clean(event.get("data", ""))}
                yield _sse(event)
        except sandbox.SandboxError as exc:
            yield _sse({"type": "error", "data": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


class RunCodeIn(BaseModel):
    language: str = Field(min_length=1, max_length=40)
    version: str | None = Field(default=None, max_length=40)
    paths: list[str] = Field(default_factory=list, max_length=50)
    code: str | None = Field(default=None, max_length=200_000)
    stdin: str = Field(default="", max_length=100_000)
    args: list[str] = Field(default_factory=list, max_length=50)


@router.post("/projects/{project_id}/run-code")
async def run_code_in_project(
    project_id: str,
    body: RunCodeIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Запустить файл проекта (или фрагмент) в Piston."""
    project = await _owned_project(session, user, project_id)
    args, problem = code_runner.validate("run_code", body.model_dump())
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    result = await code_runner.Runner(user.id, project_id, _project_root(project)).code(args)
    await audit.record(session, actor=user.id, action="project.run_code", target=project_id,
                       meta={"language": body.language, "paths": body.paths[:10]})
    await session.commit()
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/projects/{project_id}/sandbox", status_code=status.HTTP_200_OK)
async def reset_project_sandbox(
    project_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Сбросить рабочую копию проекта в песочнице: зависимости и сборки поставятся заново."""
    await _owned_project(session, user, project_id)
    try:
        await sandbox.reset(user.id, project_id)
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await audit.record(session, actor=user.id, action="project.sandbox.reset", target=project_id)
    await session.commit()
    return {"ok": True}
