"""Запуск кода из интерфейса: состояние песочницы и Piston, языки, терминал проекта."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from app.api.projects import _bound_project, _cache_id, _owned_project, _project_root
from app.db import get_session, get_sessionmaker
from app.models.code_shell import CodeShellRun
from app.models.job import Job
from app.models.user import User
from app.schemas.job import JobOut
from app.services import audit, code_lsp, code_runner, code_shell, files, jobs, preview, sandbox
from app.services.auth import get_current_user, require_admin

router = APIRouter(tags=["sandbox"])
_shell_imports: set[str] = set()


@router.get("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/lsp")
@router.get("/projects/{project_id}/lsp")
async def lsp_status(project_id: str, user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session), workspace_id: str | None = None, chat_id: str | None = None) -> dict:
    project = await _bound_project(session, user, project_id, workspace_id, chat_id)
    try:
        return await code_lsp.Client(user.id, _cache_id(project), _project_root(project)).transport("GET")
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



@router.post("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/lsp")
@router.post("/projects/{project_id}/lsp")
async def lsp_query(project_id: str, body: code_lsp.EditorQuery,
                    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session), workspace_id: str | None = None, chat_id: str | None = None) -> dict:
    project = await _bound_project(session, user, project_id, workspace_id, chat_id)
    try:
        result = await code_lsp.Client(user.id, _cache_id(project), _project_root(project)).call(
            code_lsp.Query(**body.model_dump(exclude={"content"})), body.content,
        )
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit.record(session, actor=user.id, action="project.lsp", target=project_id,
                       meta={"operation": body.operation, "path": body.path})
    await session.commit()
    return result



@router.delete("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/lsp")
@router.delete("/projects/{project_id}/lsp")
async def lsp_stop(project_id: str, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session), workspace_id: str | None = None, chat_id: str | None = None) -> dict:
    project = await _bound_project(session, user, project_id, workspace_id, chat_id)
    try:
        return await code_lsp.Client(user.id, _cache_id(project), _project_root(project)).transport("DELETE")
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



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
    shell: Literal["bash", "powershell"] = "bash"
    cwd: str = Field(default=".", max_length=4096)


def _sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


@router.post("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/run")
@router.post("/projects/{project_id}/run")
async def run_in_project(
    project_id: str,
    body: RunIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker=Depends(get_sessionmaker), workspace_id: str | None = None, chat_id: str | None = None) -> StreamingResponse:
    """Терминал проекта: команда в песочнице, вывод — потоком (SSE)."""
    project = await _bound_project(session, user, project_id, workspace_id, chat_id)
    root = _project_root(project)
    await audit.record(session, actor=user.id, action="project.run", target=project_id,
                       meta={"command": body.command[:500]})
    await session.commit()

    async def stream() -> AsyncIterator[bytes]:
        try:
            box = await sandbox.info()
            options = {"collect_changes": True, "shell": body.shell, "cwd": body.cwd} if box and box.get("file_changes") else {}
            if not options and (body.shell != "bash" or body.cwd != "."):
                raise sandbox.SandboxError("Пересоберите sandbox для shell/cwd")
            async with aclosing(sandbox.run(user.id, _cache_id(project), root, body.command,
                                           timeout=body.timeout, stdin=body.stdin, **options)) as events:
                async for event in events:
                    if event.get("type") == "changes":
                        async for imported in _import_shell(maker, user, project_id, root, event, workspace_id, chat_id):
                            yield _sse(imported)
                    else:
                        if event.get("type") == "output":
                            event = {**event, "data": sandbox.clean(event.get("data", ""))}
                        yield _sse(event)
        except sandbox.SandboxError as exc:
            yield _sse({"type": "error", "data": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})



async def _import_shell(maker, user, project_id, root, packet, workspace_id=None, chat_id=None):
    runner = code_runner.Runner(user.id, workspace_id or project_id, root)
    async with maker() as session:
        await _bound_project(session, user, project_id, workspace_id, chat_id)
        job = CodeShellRun(owner_id=user.id, project_id=project_id, workspace_id=workspace_id, status="running", result={"checkpoints": [],
                            "expected": {item["path"]: item["after_sha256"] for item in packet.get("changes", [])},
                            "expected_modes": {item["path"]: item.get("mode") for item in packet.get("changes", [])}})
        session.add(job)
        await session.commit()
        _shell_imports.add(job.id)
        finished = False
        try:
            async for item in code_shell.apply(runner, packet, {"id": job.id, "name": "shell_file"}):
                if "checkpoint" in item:
                    await _bound_project(session, user, project_id, workspace_id, chat_id)
                    job.result = {**job.result, "checkpoints": [*job.result["checkpoints"], item["checkpoint"]]}
                    await session.commit()
                elif "tool" in item:
                    yield {"type": "file_change", "change": item["tool"]["change"]}
                elif "shell_result" in item:
                    result = item["shell_result"]
                    job.status, job.error = ("error" if result.get("error") else "done"), result.get("error")
                    job.result = {**job.result, "summary": result}
                    await audit.record(session, actor=user.id, action="project.shell.import", target=project_id,
                                       meta={"run_id": job.id, "paths": [change["path"] for change in result.get("applied", [])]})
                    await session.commit()
                    finished = True
                    sandbox.preview_touch(user.id, workspace_id or project_id)
                    yield {"type": "files", "run_id": job.id, **result}
        finally:
            _shell_imports.discard(job.id)
            if not finished:
                with anyio.CancelScope(shield=True):
                    job.status, job.error = "error", "Импорт прервался; checkpoint сохранён, пакет доступен для повторного применения"
                    await session.commit()


@router.get("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/command-changes")
@router.get("/projects/{project_id}/command-changes")
async def pending_shell(project_id: str, user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session), workspace_id: str | None = None, chat_id: str | None = None) -> dict:
    project = await _bound_project(session, user, project_id, workspace_id, chat_id)
    try:
        packet = await code_shell.fetch(code_runner.Runner(user.id, _cache_id(project), _project_root(project)))
        return await run_in_threadpool(code_shell.describe, packet, _project_root(project))
    except (ValueError, OSError, sandbox.SandboxError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc



@router.post("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/command-changes")
@router.post("/projects/{project_id}/command-changes")
async def resolve_shell(project_id: str, body: code_runner.CommandChanges, user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session), maker=Depends(get_sessionmaker), workspace_id: str | None = None, chat_id: str | None = None) -> dict:
    project = await _bound_project(session, user, project_id, workspace_id, chat_id)
    runner = code_runner.Runner(user.id, _cache_id(project), _project_root(project))
    try:
        packet = await code_shell.fetch(runner)
        if not body.change_id or body.change_id != packet.get("id"):
            raise ValueError("Пакет изменений сменился; обновите список")
        if body.action == "discard":
            await code_shell.acknowledge(runner, packet)
            await audit.record(session, actor=user.id, action="project.shell.discard", target=project_id,
                               meta={"change_id": body.change_id})
            await session.commit()
            return {"discarded": body.change_id}
        if body.action != "apply":
            raise ValueError("Нужно apply или discard")
        result = {"changes": []}
        async for event in _import_shell(maker, user, project_id, runner.root, packet, workspace_id, chat_id):
            if event["type"] == "file_change":
                result["changes"].append(event["change"])
            else:
                result.update(event)
        return result
    except (ValueError, OSError, sandbox.SandboxError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc



@router.get("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/shell-history")
@router.get("/projects/{project_id}/shell-history")
async def shell_history(project_id: str, user: User = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session), workspace_id: str | None = None, chat_id: str | None = None) -> list[dict]:
    await _bound_project(session, user, project_id, workspace_id, chat_id)
    rows = list(await session.scalars(select(CodeShellRun).where(CodeShellRun.owner_id == user.id,
                                     CodeShellRun.project_id == project_id, CodeShellRun.workspace_id == workspace_id).order_by(CodeShellRun.created_at.desc()).limit(100)))
    for row in rows:
        if row.status == "running" and row.id not in _shell_imports:
            row.status = "interrupted"
    await session.commit()
    return [{"id": row.id, "status": row.status, "rolled_back": row.result.get("rolled_back", False),
             "paths": [point["path"] for point in row.result.get("checkpoints", [])]}
            for row in rows if row.result.get("checkpoints")]



@router.post("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/shell-history/{run_id}/rollback")
@router.post("/projects/{project_id}/shell-history/{run_id}/rollback")
async def rollback_shell(project_id: str, run_id: str, user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session), workspace_id: str | None = None, chat_id: str | None = None) -> dict:
    project = await _bound_project(session, user, project_id, workspace_id, chat_id)
    row = await session.get(CodeShellRun, run_id)
    if row is None or row.owner_id != user.id or row.project_id != project_id or row.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    if row.id in _shell_imports or row.result.get("rolled_back"):
        raise HTTPException(status_code=409, detail="Запуск работает или уже отменён")
    root = _project_root(project)
    try:
        # Check every path before restoring any; never discard later editor changes silently.
        for point in row.result.get("checkpoints", []):
            try:
                current = await run_in_threadpool(files.read_blob, root, point["path"])
            except FileNotFoundError:
                current = {"sha256": None, "mode": None}
            expected_mode = row.result.get("expected_modes", {}).get(point["path"])
            if current["sha256"] != row.result["expected"][point["path"]] or expected_mode is not None and current["mode"] != expected_mode:
                raise ValueError(point["path"] + ": файл изменился после команды; автоматический откат отклонён")
        for point in reversed(row.result.get("checkpoints", [])):
            await run_in_threadpool(code_shell.restore, root, point, expected_sha256=row.result["expected"][point["path"]],
                                    expected_mode=row.result.get("expected_modes", {}).get(point["path"]))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row.result = {**row.result, "rolled_back": True}
    await audit.record(session, actor=user.id, action="project.shell.rollback", target=project_id, meta={"run_id": run_id})
    await session.commit()
    sandbox.preview_touch(user.id, _cache_id(project))
    return {"restored": [point["path"] for point in row.result.get("checkpoints", [])]}



class RunCodeIn(BaseModel):
    language: str = Field(min_length=1, max_length=40)
    version: str | None = Field(default=None, max_length=40)
    paths: list[str] = Field(default_factory=list, max_length=50)
    code: str | None = Field(default=None, max_length=200_000)
    stdin: str = Field(default="", max_length=100_000)
    args: list[str] = Field(default_factory=list, max_length=50)


@router.post("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/run-code")
@router.post("/projects/{project_id}/run-code")
async def run_code_in_project(
    project_id: str,
    body: RunCodeIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session), workspace_id: str | None = None, chat_id: str | None = None) -> dict:
    """Запустить файл проекта (или фрагмент) в Piston."""
    project = await _bound_project(session, user, project_id, workspace_id, chat_id)
    args, problem = code_runner.validate("run_code", body.model_dump())
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    result = await code_runner.Runner(user.id, _cache_id(project), _project_root(project)).code(args)
    await audit.record(session, actor=user.id, action="project.run_code", target=project_id,
                       meta={"language": body.language, "paths": body.paths[:10]})
    await session.commit()
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result



@router.delete("/projects/{project_id}/chats/{chat_id}/workspaces/{workspace_id}/sandbox", status_code=status.HTTP_200_OK)
@router.delete("/projects/{project_id}/sandbox", status_code=status.HTTP_200_OK)
async def reset_project_sandbox(
    project_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session), workspace_id: str | None = None, chat_id: str | None = None) -> dict:
    """Сбросить рабочую копию проекта в песочнице: зависимости и сборки поставятся заново."""
    project = await _bound_project(session, user, project_id, workspace_id, chat_id)
    try:
        await sandbox.reset(user.id, _cache_id(project))
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await audit.record(session, actor=user.id, action="project.sandbox.reset", target=project_id)
    await session.commit()
    return {"ok": True}



# --- превью приложения -----------------------------------------------------------

class PreviewIn(BaseModel):
    command: str | None = Field(default=None, max_length=4000)


def _with_url(request: Request, owner_id: str, project_id: str, data: dict) -> dict:
    """Ссылка на превью для браузера: отдельный адрес, вход по подписанному токену."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost").split(",")[0].strip()
    base = preview.base_url(scheme, host)
    out = {**data, "base": base}
    nonce = data.pop("nonce", None)
    out.pop("nonce", None)
    if nonce and data.get("state") in ("starting", "running"):
        out["url"] = preview.open_url(base, preview.make_token(owner_id, project_id, nonce))
    return out


@router.post("/projects/{project_id}/preview")
async def start_preview(
    project_id: str,
    request: Request,
    body: PreviewIn | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Запустить приложение проекта в песочнице и получить ссылку на живое превью."""
    project = await _owned_project(session, user, project_id)
    command = (body.command if body else None) or None
    try:
        data = await sandbox.preview_start(user.id, project_id, _project_root(project), command)
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit.record(session, actor=user.id, action="project.preview.start", target=project_id,
                       meta={"command": (data.get("command") or "")[:500]})
    await session.commit()
    return _with_url(request, user.id, project_id, data)


@router.get("/projects/{project_id}/preview")
async def preview_state(
    project_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await _owned_project(session, user, project_id)
    try:
        data = await sandbox.preview_status(user.id, project_id, logs=True, root=_project_root(project))
    except sandbox.SandboxError as exc:
        return {"state": "unavailable", "error": str(exc)}
    return _with_url(request, user.id, project_id, data)


@router.delete("/projects/{project_id}/preview")
async def stop_preview(
    project_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _owned_project(session, user, project_id)
    try:
        return await sandbox.preview_stop(user.id, project_id)
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
