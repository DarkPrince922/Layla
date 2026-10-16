"""Durable, independently addressable Code agents and sandbox commands."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import aclosing
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only
from starlette.concurrency import run_in_threadpool

from app.models.code_team import CodeTeam
from app.models.code_worker import CodeWorker
from app.models.job import Job
from app.models.provider import Provider
from app.models.user import Project
from app.services import (
    code_hooks,
    code_lsp,
    code_runner,
    code_sessions,
    code_shell,
    code_teams,
    code_usage,
    code_worktrees,
    jobs,
    provider_client,
    sandbox,
)

ACTIVE = frozenset({"queued", "running"})
OUTPUT_LIMIT = 80000


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentStart(NoArgs):
    task: str = Field(min_length=10, max_length=12000)
    role: Literal["implement", "explore", "review"] = "implement"
    detached: bool = False
    require_plan: bool = False
    name: str | None = Field(default=None, pattern=code_teams.NAME)


class CommandStart(NoArgs):
    command: str = Field(min_length=1, max_length=8000)
    timeout: int = Field(default=120, ge=1, le=sandbox.MAX_TIMEOUT)
    stdin: str | None = Field(default=None, max_length=100000)
    detached: bool = False
    shell: Literal["bash", "powershell"] = "bash"
    cwd: str = Field(default=".", max_length=4096)
    interactive: bool = False
    tty: bool = False
    rows: int = Field(default=24, ge=2, le=300)
    cols: int = Field(default=80, ge=10, le=500)


class WorkerId(NoArgs):
    worker_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class CommandText(NoArgs):
    text: str = Field(default="", max_length=100000)
    eof: bool = False
    rows: int | None = Field(default=None, ge=2, le=300)
    cols: int | None = Field(default=None, ge=10, le=500)


class CommandInput(WorkerId, CommandText):
    pass


class WorkerCommandChanges(WorkerId, code_runner.CommandChanges):
    pass


class WorkerOutput(WorkerId):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12000, ge=1, le=40000)
    path: str | None = Field(default=None, max_length=4096)
    include_diff: bool = True


class WorkerWait(WorkerId):
    timeout: int = Field(default=30, ge=0, le=60)


class WorkerApply(WorkerId):
    paths: list[str] | None = Field(default=None, min_length=1, max_length=200)


class WorkerDiscard(WorkerId):
    discard_changes: bool = False


SCHEMAS = {
    "agent_start": AgentStart, "command_start": CommandStart,
    "worker_list": NoArgs, "worker_output": WorkerOutput, "worker_wait": WorkerWait,
    "worker_stop": WorkerId, "worker_resume": WorkerId, "worker_apply": WorkerApply,
    "worker_discard": WorkerDiscard, "command_input": CommandInput, "worker_command_changes": WorkerCommandChanges,
}
DESCRIPTIONS = {
    "agent_start": "Start an independent Code agent in a private Git worktree snapshot of current project files (uncommitted files included). role=implement may edit its copy; explore/review inspect it. Returns worker_id immediately. At most 3 active workers per chat. No recursive delegation. Use worker_wait/output, then worker_apply to import reviewed changes. Attached workers are awaited before the lead finishes and stopped if it is cancelled; detached=true explicitly allows work to outlive the lead.",
    "command_start": "Start a background sandbox command in a private worktree with durable output. Returns worker_id immediately. Created/edited/deleted files are collected into the worker copy; use worker_apply to import them into the main project with diffs/rollback. shell is bash or powershell when installed; cwd is project-relative. tty=true allocates a real PTY; rows/cols set its size. interactive=true keeps stdin open for REPLs (python -u -i, node -i, bash); interactive workers are detached so the chat stays responsive. Use command_input, worker_output/wait/stop.",
    "command_input": "Send exact text to a running interactive command's stdin. Include newline to submit a REPL expression; eof=true closes stdin. The same process retains variables/current directory. Only this chat's command workers are accessible. Never claims success if stdin is closed or the process restarted.",
    "worker_command_changes": "Inspect/retry/discard a finished command worker's retained sandbox packet after an interrupted import or export limit. action=apply collects eligible results into that worker's private copy, not the main project; worker_apply imports them into the main project separately. discard requires the current change_id from list.",
    "worker_list": "List this Code chat's workers, progress, reports, changed files and whether their changes were applied. Other chats/users/projects are inaccessible.",
    "worker_output": "Read a worker's persisted output using character offset/limit, recent tool events, report and diffs. Works after navigation or restart.",
    "worker_wait": "Wait up to 60 seconds for one worker; return status, output and changed files. May be called repeatedly. It does not restart the worker or consume additional model calls internally.",
    "worker_stop": "Stop one worker and its sandbox execution. Preserve the worktree, output and completed edits for review or resume.",
    "worker_resume": "Resume an interrupted/cancelled/failed Code agent in its existing private worktree using saved progress. For a command, explicitly reruns the command from its beginning. Rechecks permissions and reserves one of the 3 slots.",
    "worker_apply": "Import all or selected reviewed text changes from a finished implementing worker into the main project. Preserves concurrent edits: every file must still match its snapshot hash (or already match the result). Returns diffs and creates normal rollback checkpoints. Conflicts retain the worker copy; inspect its diff and resolve with regular file tools.",
    "worker_discard": "Remove a finished worker's private worktree and sandbox cache; keep its persisted report/output. Unapplied changes require discard_changes=true. Never deletes main project files.",
}
TOOLS = [{"type": "function", "function": {
    "name": name, "description": DESCRIPTIONS[name], "parameters": schema.model_json_schema(),
}} for name, schema in SCHEMAS.items()]


def public(row: CodeWorker, *, detail=False) -> dict:
    if row.summary and not detail:
        return {**row.summary, "id": row.id, "job_id": row.job_id, "status": row.status, "error": row.error}
    data = {"id": row.id, "job_id": row.job_id, "kind": row.kind, "role": row.role,
            "task": row.task, "status": row.status, "report": row.report, "error": row.error,
            "team_id": row.team_id, "member_name": row.member_name,
            "changes": [code_worktrees.public_change(change) for change in row.changes],
            "applied": row.applied, "events": [
                {key: value for key, value in event.items() if key not in ("output", "command")}
                for event in row.events[-8:]
            ],
            "detached": row.payload.get("detached", False),
            "interactive": row.payload.get("interactive", False),
            "pending_shell": row.payload.get("pending_shell"),
            "workspace_removed": row.payload.get("workspace_removed", False)}
    if detail:
        data["changes"] = row.changes
    return data


def summarize(summary: dict, values: dict) -> dict:
    summary = dict(summary)
    for key in ("kind", "role", "task", "status", "error", "applied"):
        if key in values:
            summary[key] = values[key]
    if "report" in values:
        summary["report"] = values["report"][:1000]
    if "events" in values:
        summary["events"] = [{key: value for key, value in event.items() if key not in ("output", "command")}
                             for event in values["events"][-8:]]
    if "changes" in values:
        summary["changes"] = [code_worktrees.public_change(change) for change in values["changes"]]
    if "payload" in values:
        for key in ("detached", "workspace_removed", "interactive", "pending_shell"):
            summary[key] = values["payload"].get(key, None if key == "pending_shell" else False)
    return summary


class Manager:
    def __init__(self, runtime, root: str, permissions=None, *, provider_id=None, model=None,
                 mode="auto", caps=None, source_root=None, workspace_id=None):
        self.runtime, self.root, self.permissions = runtime, root, permissions
        self.maker = runtime.maker
        self.owner_id, self.chat_id, self.project_id = runtime.owner_id, runtime.chat_id, runtime.project_id
        self.provider_id, self.model, self.mode = provider_id, model, mode
        self.caps = dict(caps or {})
        self.attached: set[str] = set()
        self.tool_allowlist = None
        self.source_root, self.workspace_id = source_root or root, workspace_id
        self.team = code_teams.Runtime(self)
        self.transcript = None

    async def authorize(self, session):
        await self.runtime._authorize(session)
        project = await session.get(Project, self.project_id)
        if project.path != self.source_root:
            raise ValueError("Каталог проекта изменился; откройте чат заново")
        if self.workspace_id:
            from app.models.code_workspace import CodeWorkspace
            from app.services.code_workspace import paths
            workspace = await session.get(CodeWorkspace, self.workspace_id)
            if (workspace is None or workspace.owner_id != self.owner_id or workspace.chat_id != self.chat_id
                    or workspace.project_id != self.project_id or workspace.status not in ("active", "kept")
                    or str(paths(self.source_root, self.project_id, workspace.id)[2]) != self.root):
                raise ValueError("Рабочая копия воркера недоступна")

    def tools(self) -> list[dict]:
        if self.permissions is not None and "files.read" not in self.permissions:
            return []
        names = {"worker_list", "worker_output", "worker_wait", "agent_start"}
        if self.mode not in ("plan", "review"):
            names |= {"worker_stop", "worker_resume", "worker_discard"}
            if self.permissions is None or "files.write" in self.permissions:
                names.add("worker_apply")
        if self.mode != "plan" and code_runner.can_run(self.permissions):
            names.add("command_start")
            if self.mode != "review":
                names.add("worker_command_changes")
                names.add("command_input")
        return [tool for tool in TOOLS if tool["function"]["name"] in names]

    async def owned(self, session, worker_id: str) -> CodeWorker:
        await self.authorize(session)
        row = await session.scalar(select(CodeWorker).where(
            CodeWorker.id == worker_id, CodeWorker.owner_id == self.owner_id,
            CodeWorker.chat_id == self.chat_id, CodeWorker.project_id == self.project_id,
        ))
        if row is None:
            raise ValueError("Воркер не найден в этом Code-чате")
        if row.payload.get("workspace_id") != self.workspace_id:
            raise ValueError("Переключитесь в рабочую копию, из которой запускался этот воркер")
        return row

    async def list(self, *, all_contexts=False) -> list[dict]:
        async with self.maker() as session:
            await self.authorize(session)
            query = select(CodeWorker).where(
                CodeWorker.chat_id == self.chat_id, CodeWorker.owner_id == self.owner_id,
                CodeWorker.project_id == self.project_id,
            )
            if not all_contexts:
                context = CodeWorker.payload["workspace_id"].as_string()
                query = query.where(context == self.workspace_id if self.workspace_id else context.is_(None))
            rows = list(await session.scalars(query.options(load_only(CodeWorker.id, CodeWorker.job_id, CodeWorker.status,
                                CodeWorker.error, CodeWorker.summary)).order_by(CodeWorker.created_at)))
            for row in rows:
                if not row.summary:
                    await session.refresh(row)
                if row.status in ACTIVE:
                    job = await session.get(Job, row.job_id) if row.job_id else None
                    if job is None or job.status not in jobs.ACTIVE or jobs.is_orphaned(job):
                        row.status, row.slot = "interrupted", None
                        row.error = "Работа прервалась. Вывод и рабочая копия сохранены; можно продолжить."
                        row.summary = {**row.summary, "status": row.status, "error": row.error}
            await session.commit()
            return [public(row) for row in rows]

    async def _reserve(self, kind: str, task: str, role: str, payload: dict, existing=None, *, allow_done=False) -> CodeWorker:
        payload = {**payload, "workspace_id": self.workspace_id}
        await self.list()  # release slots held by terminal/interrupted jobs
        for slot in range(3):
            try:
                async with self.maker() as session:
                    await self.authorize(session)
                    team_id, member_name = None, None
                    if kind == "agent" and not existing:
                        team = await session.scalar(select(CodeTeam).where(
                            CodeTeam.chat_id == self.chat_id, CodeTeam.active_slot == 1).with_for_update())
                        if team:
                            team_id = team.id
                            member_name = payload.get("name") or "agent-" + uuid4().hex[:10]
                            if member_name == "lead" or await session.scalar(select(CodeWorker.id).where(
                                    CodeWorker.team_id == team.id, CodeWorker.member_name == member_name)):
                                raise ValueError("Имя участника уже занято в этой команде")
                        elif payload.get("name") or payload.get("require_plan"):
                            raise ValueError("Сначала создайте команду через team_create")
                    worker_id, job_id = existing or str(uuid4()), str(uuid4())
                    job = Job(id=job_id, owner_id=self.owner_id, domain="code", kind="code.worker",
                              title=("Агент: " if kind == "agent" else "Команда: ") + task[:230],
                              status="queued", result={"code_chat_id": self.chat_id, "worker_id": worker_id})
                    session.add(job)
                    await session.flush()
                    if existing:
                        row = await self.owned(session, existing)
                        if row.team_id:
                            current_team = await session.scalar(select(CodeTeam).where(CodeTeam.id == row.team_id).with_for_update())
                            if current_team is None or current_team.active_slot != 1:
                                raise ValueError("Команда уже закрыта")
                        if row.status in ACTIVE or row.status == "done" and not allow_done:
                            raise ValueError("Этот воркер уже работает или завершён")
                        changed = await session.execute(update(CodeWorker).where(
                            CodeWorker.id == existing, CodeWorker.status == row.status,
                        ).values(slot=slot, status="queued", job_id=job_id, error=None, applied={}, role=role,
                                 payload={**row.payload, **payload},
                                 summary={**row.summary, "role": role, "status": "queued", "error": None, "applied": {}}))
                        if changed.rowcount != 1:
                            raise ValueError("Воркер уже запущен параллельным запросом")
                        await session.refresh(row)
                    else:
                        row = CodeWorker(id=worker_id, owner_id=self.owner_id, chat_id=self.chat_id,
                                         project_id=self.project_id, job_id=job_id, slot=slot,
                                         kind=kind, role=role, task=task, status="queued", payload=payload,
                                         output="", report="", events=[], changes=[], applied={},
                                         team_id=team_id, member_name=member_name)
                        row.summary = {"kind": kind, "role": role, "task": task, "status": "queued",
                                       "report": "", "error": None, "changes": [], "applied": {}, "events": [],
                                       "detached": payload.get("detached", False), "interactive": payload.get("interactive", False), "workspace_removed": False,
                                       "team_id": team_id, "member_name": member_name}
                        session.add(row)
                    await session.commit()
                jobs.launch(self.maker, job_id, lambda handle, wid=worker_id: self._execute(wid, handle))
                if not payload.get("detached"):
                    self.attached.add(worker_id)
                return row
            except IntegrityError:
                continue
        raise ValueError("В этом чате уже работают 3 воркера. Дождитесь результата или остановите один.")

    def _allow_implement(self, role: str):
        if role == "implement" and (self.mode in ("plan", "review") or
                                    self.permissions is not None and "files.write" not in self.permissions):
            raise ValueError("Режим или персона разрешает только исследование/ревью, без реализации")

    async def start_agent(self, args: AgentStart, *, tool_allowlist=None, skill_hooks=None) -> dict:
        self._allow_implement(args.role)
        if not self.provider_id or not self.model:
            raise ValueError("Для воркера нужен провайдер и модель Code-чата")
        row = await self._reserve("agent", args.task, "explore" if args.require_plan else args.role, {
            "provider_id": self.provider_id, "model": self.model, "caps": self.caps, "name": args.name,
            "permissions": self.permissions, "detached": args.detached, "mode": self.mode,
            "tool_allowlist": sorted(set(tool_allowlist) & set(self.tool_allowlist))
            if tool_allowlist is not None and self.tool_allowlist is not None
            else tool_allowlist if tool_allowlist is not None else self.tool_allowlist,
            "skill_hooks": skill_hooks or [], "require_plan": args.require_plan, "requested_role": args.role,
        })
        return {"worker": public(row)}

    async def start_command(self, args: CommandStart) -> dict:
        if self.mode == "plan" or not code_runner.can_run(self.permissions):
            raise ValueError("Запуск команд недоступен в этом режиме или персоне")
        if await sandbox.info() is None:
            raise ValueError("Песочница не подключена; команда не запущена")
        row = await self._reserve("command", args.command, "command", {
            **args.model_dump(), "detached": args.detached or args.interactive,
            "permissions": self.permissions, "mode": self.mode,
        })
        return {"worker": public(row)}

    async def input(self, args: CommandInput):
        if self.mode in ("plan", "review") or not code_runner.can_run(self.permissions):
            raise ValueError("Ввод в процесс недоступен в этом режиме/персоне")
        async with self.maker() as session:
            row = await self.owned(session, args.worker_id)
            if row.kind != "command" or row.status not in ACTIVE or not row.payload.get("interactive"):
                raise ValueError("Это не работающий интерактивный command-воркер")
        if args.rows is not None or args.cols is not None:
            await sandbox.shell_request(self.owner_id, args.worker_id, "POST", "resize", {"rows": args.rows or 24, "cols": args.cols or 80})
        return await sandbox.shell_request(self.owner_id, args.worker_id, "POST", "input",
                                           {"text": args.text, "eof": args.eof})

    async def output(self, args: WorkerOutput) -> dict:
        await self.list()
        async with self.maker() as session:
            row = await self.owned(session, args.worker_id)
            worker = public(row)
            worker["report"] = row.report
            worker["events"] = row.events
            if args.include_diff:
                worker["changes"] = [{**change, "diff": change["diff"][:20000],
                                      "diff_truncated": len(change["diff"]) > 20000}
                                     for change in row.changes if args.path is None or change["path"] == args.path][:20]
            return {"worker": worker, "output": row.output[args.offset:args.offset + args.limit],
                    "offset": args.offset, "total_chars": len(row.output),
                    "truncated": args.offset + args.limit < len(row.output)}

    async def command_changes(self, args: WorkerCommandChanges):
        if self.mode == "plan" or not code_runner.can_run(self.permissions):
            raise ValueError("Shell-инструменты недоступны")
        async with self.maker() as session:
            row = await self.owned(session, args.worker_id)
            if row.kind != "command" or row.status in ACTIVE or row.payload.get("workspace_removed"):
                raise ValueError("Нужен завершённый command-воркер с сохранённой копией")
            payload = dict(row.payload)
        root = str(code_worktrees.paths(self.root, self.project_id, args.worker_id)[2])
        runner = code_runner.Runner(self.owner_id, args.worker_id, root)
        packet = await code_shell.fetch(runner)
        if args.action == "list":
            return code_shell.describe(packet)
        if self.mode == "review" or not args.change_id or args.change_id != packet.get("id"):
            raise ValueError("Нужен актуальный change_id; изменения недоступны в Review")
        if args.action == "discard":
            await code_shell.acknowledge(runner, packet)
            result = {"discarded": packet["id"]}
        else:
            result = {}
            async for event in code_shell.apply(runner, packet, {"id": "retry", "name": "shell_file"}):
                if "shell_result" in event:
                    result = event["shell_result"]
        remaining = await code_shell.fetch(runner)
        payload["pending_shell"] = code_shell.describe(remaining) if remaining.get("id") else None
        changes = await run_in_threadpool(code_worktrees.diff, self.root, self.project_id, args.worker_id, payload["baseline"])
        await self._save(args.worker_id, payload=payload, changes=changes)
        return result

    async def wait(self, args: WorkerWait) -> dict:
        deadline = asyncio.get_running_loop().time() + args.timeout
        while True:
            result = await self.output(WorkerOutput(worker_id=args.worker_id))
            if result["worker"]["status"] not in ACTIVE or asyncio.get_running_loop().time() >= deadline:
                return result
            await asyncio.sleep(0.5)

    async def stop(self, worker_id: str) -> dict:
        async with self.maker() as session:
            row = await self.owned(session, worker_id)
            job_id = row.job_id
        if job_id:
            await jobs.cancel(job_id, self.maker)
        async with self.maker() as session:
            row = await self.owned(session, worker_id)
            if row.status in ACTIVE or row.status == "interrupted":
                row.status, row.slot = "cancelled", None
                row.error = "Воркер остановлен; рабочая копия сохранена"
                row.summary = {**row.summary, "status": row.status, "error": row.error}
                await session.commit()
        return await self.output(WorkerOutput(worker_id=worker_id))

    async def resume(self, worker_id: str) -> dict:
        allow_done = False
        async with self.maker() as session:
            row = await self.owned(session, worker_id)
            self._allow_implement(row.role)
            if row.payload.get("workspace_removed"):
                raise ValueError("Рабочая копия удалена; запустите нового воркера")
            if row.team_id:
                team = await session.get(CodeTeam, row.team_id)
                if team is None or team.active_slot != 1:
                    raise ValueError("Команда закрыта; запустите нового воркера")
            if row.kind == "agent" and row.status == "done":
                allow_done = bool(await code_teams.Runtime(self, worker_id).pending(session))
                if not allow_done:
                    raise ValueError("Завершённому агенту сначала нужно отправить новую задачу через send_message")
            payload = {**row.payload, "permissions": self.permissions, "mode": self.mode}
            if self.tool_allowlist is not None:
                old = payload.get("tool_allowlist")
                payload["tool_allowlist"] = sorted(set(self.tool_allowlist) & set(old)) if old is not None else self.tool_allowlist
            if row.kind == "command" and (self.mode == "plan" or not code_runner.can_run(self.permissions)):
                raise ValueError("Нет права на запуск команды")
            kind, role, task = row.kind, row.role, row.task
            if payload.get("require_plan"):
                from app.models.code_team import CodeTeamRequest
                decision = await session.get(CodeTeamRequest, payload.get("plan_request_id")) if payload.get("plan_request_id") else None
                if decision and decision.status == "approved" and decision.team_id == row.team_id and decision.sender == row.member_name:
                    role = payload.get("requested_role", "implement")
                    self._allow_implement(role)
                    payload["require_plan"] = False
            if row.team_id:
                from app.models.code_team import CodeTeamRequest
                if await session.scalar(select(CodeTeamRequest.id).where(CodeTeamRequest.team_id == row.team_id,
                    CodeTeamRequest.recipient == row.member_name, CodeTeamRequest.kind == "shutdown",
                    CodeTeamRequest.status == "approved").limit(1)):
                    raise ValueError("Участник согласовал завершение; создайте нового участника")
        row = await self._reserve(kind, task, role, payload, existing=worker_id, allow_done=allow_done)
        return {"worker": public(row)}

    async def discard(self, args: WorkerDiscard) -> dict:
        async with self.maker() as session:
            row = await self.owned(session, args.worker_id)
            if row.status in ACTIVE:
                raise ValueError("Сначала остановите воркера")
            if any(change["path"] not in row.applied or row.applied[change["path"]] != change["after_sha256"]
                   for change in row.changes) and not args.discard_changes:
                raise ValueError("Есть неприменённые правки; для удаления укажите discard_changes=true")
            await code_worktrees.remove(self.root, self.project_id, row.id)
            try:
                await sandbox.reset(self.owner_id, row.id)
            except sandbox.SandboxError:
                pass
            row.payload = {**row.payload, "workspace_removed": True}
            row.summary = {**row.summary, "workspace_removed": True}
            await session.commit()
        return {"discarded": args.worker_id}

    async def proposals(self, args: WorkerApply) -> list[dict]:
        if self.mode in ("plan", "review") or self.permissions is not None and "files.write" not in self.permissions:
            raise ValueError("Нет права применять изменения проекта")
        async with self.maker() as session:
            row = await self.owned(session, args.worker_id)
            if row.status in ACTIVE or row.role != "implement" and row.kind != "command":
                raise ValueError("Применять можно только завершённого реализующего воркера")
            if row.payload.get("workspace_removed"):
                raise ValueError("Рабочая копия удалена")
            selected = set(args.paths) if args.paths is not None else {change["path"] for change in row.changes}
            if not selected <= {change["path"] for change in row.changes}:
                raise ValueError("Указанный файл не входит в изменения воркера")
            changes = [{**change, "before_sha256": row.applied.get(change["path"], change["before_sha256"])}
                       for change in row.changes if change["path"] in selected]
        # Preflight every selected file before the first write. Per-file CAS is
        # repeated at application, so an editor race cannot overwrite new work.
        return [await run_in_threadpool(code_worktrees.proposal, self.root, self.project_id, args.worker_id, change)
                for change in changes]

    async def mark_applied(self, worker_id: str, path: str, sha: str | None):
        async with self.maker() as session:
            row = await self.owned(session, worker_id)
            await run_in_threadpool(code_worktrees.advance_baseline, self.root, self.project_id, worker_id, path, sha)
            row.applied = {**row.applied, path: sha}
            row.summary = {**row.summary, "applied": row.applied}
            baseline = dict(row.payload["baseline"])
            if sha is None:
                baseline.pop(path, None)
            else:
                baseline[path] = sha
            row.payload = {**row.payload, "baseline": baseline}
            await session.commit()

    async def call(self, name: str, arguments: dict) -> dict:
        try:
            if name not in {tool["function"]["name"] for tool in self.tools()}:
                raise ValueError("Инструмент недоступен в текущем режиме или персоне")
            args = SCHEMAS[name].model_validate(arguments)
            if name == "agent_start":
                return await self.start_agent(args)
            if name == "command_start":
                return await self.start_command(args)
            if name == "command_input":
                return await self.input(args)
            if name == "worker_command_changes":
                return await self.command_changes(args)
            if name == "worker_list":
                return {"workers": await self.list()}
            if name == "worker_output":
                return await self.output(args)
            if name == "worker_wait":
                return await self.wait(args)
            if name == "worker_stop":
                return await self.stop(args.worker_id)
            if name == "worker_resume":
                return await self.resume(args.worker_id)
            if name == "worker_discard":
                return await self.discard(args)
            raise ValueError("Применение правок выполняется через checkpoint-aware обработчик")
        except (ValueError, OSError) as exc:
            return {"error": str(exc)[:1500]}

    async def finish_attached(self) -> dict | None:
        if not self.attached:
            return None
        ids = set(self.attached)
        results = []
        for worker_id in sorted(ids):
            while True:
                if self.transcript is not None:
                    async with self.maker() as session:
                        if await self.team.pending(session):
                            return {"team_mail_pending": True, "workers_completed": results}
                    incoming = None
                else:
                    incoming = await self.team.receive()
                if incoming:
                    return {"team_mail": incoming["content"], "workers_completed": results}
                result = await self.wait(WorkerWait(worker_id=worker_id, timeout=0))
                if result["worker"]["status"] not in ACTIVE:
                    summary = {**result["worker"], "changes": [code_worktrees.public_change(change)
                                                               for change in result["worker"]["changes"]]}
                    summary["events"] = [{key: value for key, value in event.items()
                                          if key not in ("output", "command")}
                                         for event in result["worker"]["events"][-8:]]
                    results.append({"worker": summary, "output_tail": result["output"][-6000:]})
                    self.attached.discard(worker_id)
                    break
                await asyncio.sleep(0.5)
        return {"workers_completed": results}

    async def cancel_attached(self):
        for worker_id in list(self.attached):
            try:
                await self.stop(worker_id)
            except ValueError:
                pass
        self.attached.clear()

    async def _save(self, worker_id: str, **values):
        async with self.maker() as session:
            await self.authorize(session)
            summary = await session.scalar(select(CodeWorker.summary).where(CodeWorker.id == worker_id))
            values["summary"] = summarize(summary or {}, values)
            await session.execute(update(CodeWorker).where(
                CodeWorker.id == worker_id, CodeWorker.owner_id == self.owner_id,
            ).values(**values))
            await session.commit()

    async def _execute(self, worker_id: str, handle):
        from app.services import project_agent

        output, report, events, changes = "", "", [], []
        payload = {}
        status, error = "done", None
        initialized = False
        runner = None
        saved_at = 0.0
        try:
            async with self.maker() as session:
                row = await self.owned(session, worker_id)
                payload, kind, task, role = dict(row.payload), row.kind, row.task, row.role
                output, events = row.output, list(row.events)
            await self._save(worker_id, status="running")
            await handle.step("Готовлю отдельную рабочую копию", progress=0.05)
            if not payload.get("initialized"):
                payload.update(await code_worktrees.create(self.root, self.project_id, worker_id))
                initialized = True
                await self._save(worker_id, payload=payload)
            initialized = True
            worktree = str(code_worktrees.paths(self.root, self.project_id, worker_id)[2])
            if not Path(worktree).is_dir():
                raise ValueError("Рабочая копия отсутствует; исходные файлы не подменяются новым снимком")
            runner = code_runner.Runner(self.owner_id, worker_id, worktree)
            if kind == "command":
                await handle.step("Команда работает в собственной песочнице", progress=0.2)
                async for item in runner.command(code_runner.RunCommand(
                    command=task, timeout=payload["timeout"], stdin=payload.get("stdin"),
                    shell=payload.get("shell", "bash"), cwd=payload.get("cwd", "."),
                    interactive=payload.get("interactive", False), tty=payload.get("tty", False),
                    rows=payload.get("rows", 24), cols=payload.get("cols", 80),
                )):
                    if "output" in item:
                        output = item["output"][-OUTPUT_LIMIT:]
                        await self._save(worker_id, output=output)
                    if "result" in item:
                        result = item["result"]
                        packet = result.pop("shell_packet", None)
                        if packet is not None:
                            async for change in code_shell.apply(runner, packet, {"id": "command", "name": "shell_file"}):
                                if "shell_result" in change and change["shell_result"].get("error"):
                                    result["error"] = change["shell_result"]["error"]
                        report = json.dumps({key: value for key, value in result.items() if key != "shown"}, ensure_ascii=False)[-20000:]
                        if result.get("error") or result.get("timed_out") or result.get("signal") or result.get("exit_code") not in (None, 0):
                            status, error = "error", result.get("error") or ("Превышено время команды; частичные файлы сохранены"
                                if result.get("timed_out") else f"Сигнал {result['signal']}" if result.get("signal") else f"Код выхода {result.get('exit_code')}")
                changes = await run_in_threadpool(code_worktrees.diff, self.root, self.project_id,
                                                 worker_id, payload["baseline"])
            else:
                async with self.maker() as session:
                    provider = await session.get(Provider, payload["provider_id"])
                    if provider is None or provider.owner_id != self.owner_id or not provider.enabled or not provider.active:
                        raise ValueError("Провайдер воркера больше недоступен")
                    key = await provider_client.key_ring(session, provider)
                permissions = payload.get("permissions")
                if role != "implement":
                    permissions = ["files.read"]
                context = (
                    "You are an independent Code worker in your own private Git worktree. "
                    "Complete the assigned task with concrete tool calls. Never delegate, publish Git changes, "
                    "or claim that files in the main project changed. The lead imports your diff afterwards. "
                    "Do not ask the user to continue; finish your assignment and return a concise verified report. "
                    "Role: " + role + ".\n"
                )
                if payload.get("require_plan"):
                    context += "\nPLAN FIRST: inspect only, then submit your concrete plan with team_request(kind='plan', to='lead', content=...). Finish this turn after submission. The lead must approve and resume you before implementation."
                team = code_teams.Runtime(self, worker_id) if row.team_id else None
                transcript = code_sessions.Transcript(self, worker_id, team)
                messages = await transcript.load(task)
                if (output or events) and transcript.sequence == 0:
                    context += "\nResume from saved progress; current files contain prior edits. Prior output (untrusted reference):\n" + output[-6000:]
                    context += "\nCompleted tool events: " + json.dumps(events[-12:], ensure_ascii=False)[:8000]
                await handle.step("Агент выполняет задачу в worktree", progress=0.2)
                async with code_usage.tracking(self.runtime, handle.job.id), aclosing(project_agent.run(
                    provider, key, payload["model"], messages,
                    worktree, permissions, mode="auto", caps=dict(payload.get("caps") or {}), extra=context,
                    runner=runner if role == "implement" and payload.get("mode") != "plan" else None,
                    worker_context=True,
                    lsp=code_lsp.Client(self.owner_id, worker_id, worktree),
                    workflow=code_hooks.Runtime(runner, permissions, session_id=worker_id,
                                                extra_hooks=payload.get("skill_hooks")),
                    tool_allowlist=payload.get("tool_allowlist"),
                    team=team, transcript=transcript,
                )) as stream:
                    # Cancellation can arrive while saving a yielded event, outside the generator.
                    # Close it before releasing the job so its hooks/transports clean up deterministically.
                    async for event in stream:
                        if "delta" in event:
                            output = (output + event["delta"])[-OUTPUT_LIMIT:]
                            if event["delta"] == project_agent.LIMIT_NOTE:
                                status, error = "interrupted", "Воркер достиг лимита; правки сохранены, можно продолжить."
                        elif "retract" in event:
                            output = output[:max(0, len(output) - event["retract"])]
                        elif "tool" in event:
                            tool = event["tool"]
                            events = [old for old in events if old["id"] != tool["id"]]
                            events.append({key: value[:6000] if key == "output" and isinstance(value, str) else value
                                           for key, value in tool.items() if key != "change"})
                            events = events[-60:]
                        important = event.get("tool", {}).get("status") in ("done", "error", "rejected")
                        if important or time.monotonic() - saved_at >= 0.5:
                            await self._save(worker_id, output=output, events=events)
                            saved_at = time.monotonic()
                report = output[-20000:]
                if role == "implement":
                    changes = await run_in_threadpool(code_worktrees.diff, self.root, self.project_id,
                                                     worker_id, payload["baseline"])
            if status != "done":
                raise RuntimeError(error or "Воркер завершился с ошибкой")
        except asyncio.CancelledError:
            status, error = "interrupted", "Воркер остановлен или сервер перезапущен. Правки и вывод сохранены."
            raise
        except Exception as exc:
            if status == "done":
                status = "error"
            error = str(exc)[:1500] if isinstance(exc, ValueError) else jobs.public_error(exc)
            raise
        finally:
            if runner is not None:
                await runner.close()
            if runner is not None and payload.get("command") and runner.partial_output:
                output = runner.partial_output[-OUTPUT_LIMIT:]
            if runner is not None and payload.get("command"):
                # A cancelled stream may still have produced files. Recovery waits for sandbox cleanup.
                try:
                    packet = await code_shell.fetch(runner)
                    if packet.get("id"):
                        if packet.get("output"):
                            output = sandbox.clean(packet["output"])[-OUTPUT_LIMIT:]
                        async for _item in code_shell.apply(runner, packet, {"id": "stopped", "name": "shell_file"}):
                            if "shell_result" in _item and _item["shell_result"].get("pending_changes"):
                                payload["pending_shell"] = code_shell.describe(packet)
                except (ValueError, OSError, sandbox.SandboxError):
                    pass
            if initialized and not changes:
                try:
                    changes = await run_in_threadpool(code_worktrees.diff, self.root, self.project_id,
                                                     worker_id, payload["baseline"])
                except (ValueError, OSError):
                    pass
            try:
                await self._save(worker_id, status=status, slot=None, output=output, report=report,
                                 events=events, changes=changes, error=error, payload=payload)
                await handle.set_result({"worker_id": worker_id, "code_chat_id": self.chat_id,
                                         "status": status, "changed_files": len(changes)})
            except ValueError:
                # The owner deleted the chat/project while it was running.
                await code_worktrees.remove(self.root, self.project_id, worker_id)
