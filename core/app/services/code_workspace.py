"""Lead worktrees: durable chat binding, private Git history and branch publication."""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.models.chat import Chat
from app.models.code_workspace import CodeWorkspace
from app.models.job import Job
from app.models.user import Project
from app.services import agent_git, code_shell, code_workers, files, gitops, sandbox


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Enter(Empty):
    name: str = Field(default="work", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")


class WorkspaceId(Empty):
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class Exit(Empty):
    action: Literal["keep", "remove"] = "keep"
    discard_changes: bool = False


class Read(WorkspaceId):
    path: str = Field(min_length=1, max_length=4096)


SCHEMAS = {"enter_worktree": Enter, "exit_worktree": Exit, "resume_worktree": WorkspaceId,
           "worktree_list": Empty, "worktree_diff": WorkspaceId, "worktree_read": Read,
           "worktree_publish": WorkspaceId}
DESCRIPTIONS = {
    "enter_worktree": "Create a private Git worktree and switch this lead session into it. Copies current project files, including uncommitted edits; preserves source history when it exists. Main project files/index/branch stay in place. All following file/shell/Git/LSP/worker tools use this copy. One active worktree per chat; no nested entry. Running workers or pending command results must be resolved first.",
    "exit_worktree": "Return the lead to the original project. action=keep retains the copy/branch for resume, read/diff or publication. action=remove deletes only this chat's managed copy; dirty files or commits beyond its initial HEAD require discard_changes=true. Never publishes or imports files implicitly.",
    "resume_worktree": "Switch back to one of this chat's retained worktrees with its existing files and Git branch. Does not replace missing files with a new snapshot. Resolve active workers/command results first.",
    "worktree_list": "List this Code chat's active and retained lead worktrees, generated branches and availability. A different chat/account/project cannot access them.",
    "worktree_diff": "Inspect a worktree's complete diff since entry, including committed and uncommitted changes, without changing the main project. Review results before publishing. Use worktree_read for exact file contents.",
    "worktree_read": "Read a project-relative UTF-8 file from this chat's managed lead worktree, including a retained copy. Path traversal and symlinks are rejected. To import selected changes after exit, read current main hashes and use normal write/edit/delete tools with checkpoints.",
    "worktree_publish": "Publish a committed lead worktree only to its own generated layla/... branch, using the main project's configured HTTPS remote and owner credentials. Never merges into or force-pushes main. Commit first with git_commit while inside the copy. Without a configured remote it fails explicitly; works for retained copies too.",
}
TOOLS = [{"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name],
          "parameters": schema.model_json_schema()}} for name, schema in SCHEMAS.items()]
TRANSITIONS = frozenset({"enter_worktree", "exit_worktree", "resume_worktree"})


def paths(root, project_id, workspace_id):
    base = Path(root).absolute().parent / ".layla-code-workspaces" / str(UUID(project_id)) / str(UUID(workspace_id))
    if any(part.is_symlink() for part in (base, *base.parents)):
        raise ValueError("Каталог рабочих копий не может быть ссылкой")
    return base, base / "repository", base / "worktree"


def public(row):
    location, _repository, worktree = paths(row.source_root, row.project_id, row.id)
    return {"id": row.id, "name": row.name, "branch": row.branch, "status": row.status,
            "active": row.active_slot == 1, "available": row.status != "removed" and worktree.is_dir(),
            "path": str(worktree), "published": row.payload.get("published", False),
            "error": row.payload.get("error"), "storage": str(location)}


def _copy_snapshot(root, worktree):
    with files.export_zip(root) as source, zipfile.ZipFile(source) as incoming:
        names = {item.filename for item in incoming.infolist() if not item.is_dir()}
        with files.export_zip(worktree) as old, zipfile.ZipFile(old) as existing:
            removed = {item.filename for item in existing.infolist() if not item.is_dir()} - names
        for rel in sorted(removed):
            info = files.upload_info(worktree, rel)
            files.change_file(worktree, rel, None, info["sha256"])
        for item in incoming.infolist():
            if item.is_dir():
                continue
            info = files.upload_info(worktree, item.filename)
            files.change_file(worktree, item.filename, incoming.read(item), info["sha256"])
            os.chmod(files.safe_join(worktree, item.filename), item.external_attr >> 16 & 0o777)


async def create_files(row):
    location, repository, worktree = paths(row.source_root, row.project_id, row.id)
    location.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        source = await gitops.status(row.source_root)
        has_history = source.get("initialized") and source.get("last_commit")
        if has_history:
            await gitops._git(str(location), "clone", "--local", "--no-hardlinks", "--no-checkout", "--",
                              row.source_root, str(repository), config={"protocol.file.allow": "always"})
            await gitops._git(str(repository), "remote", "remove", "origin")
            # HEAD is preserved; source hooks/config/credentials are never copied.
            await gitops._git(str(repository), "worktree", "add", "-b", row.branch, str(worktree), "HEAD")
            await run_in_threadpool(_copy_snapshot, row.source_root, worktree)
        else:
            repository.mkdir(mode=0o700)
            await gitops._git(str(repository), "init", "-q", "-b", "snapshot")
            await run_in_threadpool(_copy_snapshot, row.source_root, repository)
            await gitops._git(str(repository), "add", "--all", "--force", "--", ".")
            await gitops._git(str(repository), "commit", "--allow-empty", "-q", "-m", "Code project snapshot",
                              config={"user.name": "Layla", "user.email": "code@localhost"})
            await gitops._git(str(repository), "worktree", "add", "-b", row.branch, str(worktree), "HEAD")
        remote = source.get("remote")
        if remote:
            await gitops.set_remote(str(worktree), gitops.validate_remote(remote))
        head = (await gitops._git(str(worktree), "rev-parse", "HEAD"))[1].strip()
        return head, {"source_history": bool(has_history)}
    except BaseException:
        await run_in_threadpool(shutil.rmtree, location, True)
        raise


class Runtime:
    def __init__(self, runtime, root, permissions=None, *, mode="auto", git=None):
        self.runtime, self.root, self.permissions, self.mode, self.git = runtime, root, permissions, mode, git
        self.runner = None
        self.job_id = None

    def tools(self):
        if self.permissions is not None and "files.read" not in self.permissions:
            return []
        names = {"worktree_list", "worktree_diff", "worktree_read"}
        if self.mode not in ("plan", "review") and agent_git.can_use(self.permissions):
            names |= TRANSITIONS | {"worktree_publish"}
        return [tool for tool in TOOLS if tool["function"]["name"] in names]

    async def authorize(self, session):
        await self.runtime._authorize(session)
        project = await session.get(Project, self.runtime.project_id)
        if project.path != self.root:
            raise ValueError("Каталог проекта изменился")

    async def owned(self, session, workspace_id):
        await self.authorize(session)
        row = await session.scalar(select(CodeWorkspace).where(CodeWorkspace.id == workspace_id,
            CodeWorkspace.owner_id == self.runtime.owner_id, CodeWorkspace.chat_id == self.runtime.chat_id,
            CodeWorkspace.project_id == self.runtime.project_id))
        if row is None or row.source_root != self.root:
            raise ValueError("Рабочая копия не найдена в этом Code-чате")
        paths(self.root, self.runtime.project_id, row.id)
        return row

    async def active(self, session):
        await self.authorize(session)
        row = await session.scalar(select(CodeWorkspace).where(CodeWorkspace.chat_id == self.runtime.chat_id,
                                   CodeWorkspace.active_slot == 1))
        return await self.owned(session, row.id) if row else None

    async def current(self):
        async with self.runtime.maker() as session:
            row = await self.active(session)
            if row is None:
                return self.root, None
            target = paths(self.root, self.runtime.project_id, row.id)[2]
            if row.status != "active" or not target.is_dir() or not gitops.is_repo(str(target)):
                raise ValueError("Активная копия недоступна; исходный проект не подставляется вместо неё")
            return str(target), row

    async def list(self):
        async with self.runtime.maker() as session:
            await self.authorize(session)
            rows = list(await session.scalars(select(CodeWorkspace).where(CodeWorkspace.chat_id == self.runtime.chat_id,
                        CodeWorkspace.owner_id == self.runtime.owner_id, CodeWorkspace.project_id == self.runtime.project_id)
                        .order_by(CodeWorkspace.created_at, CodeWorkspace.id)))
            return {"workspaces": [public(row) for row in rows]}

    async def _idle(self):
        from app.services import jobs
        async with self.runtime.maker() as session:
            await self.authorize(session)
            active = list(await session.scalars(select(Job).where(Job.chat_id == self.runtime.chat_id,
                                            Job.status.in_(jobs.ACTIVE))))
            if any(row.id != self.job_id and not jobs.is_orphaned(row) for row in active):
                raise ValueError("Дождитесь завершения задачи ведущего перед переключением копии")
        manager = code_workers.Manager(self.runtime, self.root, self.permissions)
        if any(row["status"] in code_workers.ACTIVE for row in await manager.list(all_contexts=True)):
            raise ValueError("Перед переключением остановите работающих воркеров этого чата")
        if self.runner and get_settings().sandbox_url.strip():
            box = await sandbox.info()
            if box is None or box.get("file_changes"):
                packet = await code_shell.fetch(self.runner)
                if packet.get("id"):
                    raise ValueError("Перед переключением примените или отклоните сохранённые результаты команды")

    async def enter(self, args):
        await self._idle()
        workspace_id = str(uuid4())
        async with self.runtime.maker() as session:
            await self.authorize(session)
            await session.scalar(select(Chat).where(Chat.id == self.runtime.chat_id).with_for_update())
            await self._idle()
            if await self.active(session):
                raise ValueError("Вы уже в рабочей копии; сначала выйдите из неё")
            row = CodeWorkspace(id=workspace_id, owner_id=self.runtime.owner_id, chat_id=self.runtime.chat_id,
                project_id=self.runtime.project_id, source_root=self.root, name=args.name,
                branch="layla/" + args.name + "-" + workspace_id[:8], active_slot=1, status="creating")
            session.add(row)
            await session.commit()
        try:
            head, payload = await create_files(row)
            async with self.runtime.maker() as session:
                row = await self.owned(session, workspace_id)
                row.baseline, row.payload, row.status = head, payload, "active"
                await session.commit()
            return {"workspace": public(row), "changed_workspace": True}
        except BaseException:
            async with self.runtime.maker() as session:
                row = await self.owned(session, workspace_id)
                row.status, row.active_slot = "error", None
                await session.commit()
            raise

    async def resume(self, args):
        await self._idle()
        async with self.runtime.maker() as session:
            await self.authorize(session)
            await session.scalar(select(Chat).where(Chat.id == self.runtime.chat_id).with_for_update())
            await self._idle()
            if await self.active(session):
                raise ValueError("Сначала выйдите из текущей рабочей копии")
            row = await self.owned(session, args.workspace_id)
            root = paths(self.root, self.runtime.project_id, row.id)[2]
            if row.status != "kept" or not root.is_dir() or not gitops.is_repo(str(root)):
                raise ValueError("Эту копию нельзя восстановить; сохранённые файлы не подменяются новым снимком")
            row.status, row.active_slot = "active", 1
            await session.commit()
            return {"workspace": public(row), "changed_workspace": True}

    async def exit(self, args):
        await self._idle()
        async with self.runtime.maker() as session:
            await self.authorize(session)
            await session.scalar(select(Chat).where(Chat.id == self.runtime.chat_id).with_for_update())
            await self._idle()
            row = await self.active(session)
            if row is None:
                raise ValueError("Активная рабочая копия не найдена")
            root = str(paths(self.root, self.runtime.project_id, row.id)[2])
            if args.action == "remove":
                state = await gitops.status(root)
                commits = (await gitops._git(root, "rev-list", "--count", row.baseline + "..HEAD"))[1].strip() if row.baseline else None
                if not args.discard_changes and (state.get("changes") or commits != "0"):
                    raise ValueError("Есть изменения или новые коммиты; для удаления нужно discard_changes=true")
            if args.action == "remove":
                try:
                    await sandbox.reset(self.runtime.owner_id, row.id)
                except sandbox.SandboxError:
                    pass
                await run_in_threadpool(shutil.rmtree, paths(self.root, self.runtime.project_id, row.id)[0])
                row.status = "removed"
            else:
                row.status = "kept"
            row.active_slot = None
            await session.commit()
            return {"workspace": public(row), "changed_workspace": True}

    async def diff(self, args):
        async with self.runtime.maker() as session:
            row = await self.owned(session, args.workspace_id)
            if row.status not in ("active", "kept"):
                raise ValueError("Рабочая копия недоступна")
            root = str(paths(self.root, self.runtime.project_id, row.id)[2])
            text = (await gitops._git(root, "diff", row.baseline, "--no-ext-diff", "--no-color"))[1]
            listed = (await gitops._git(root, "ls-files", "--others", "--exclude-standard", "-z"))[1]
            for rel in listed.split("\0")[:200]:
                if rel and len(text) < gitops.MAX_DIFF:
                    text += (await gitops._git(root, "diff", "--no-index", "--no-color", "--", os.devnull, rel, check=False))[1]
            text, truncated = gitops._cut(text)
            return {"workspace": public(row), "diff": text, "truncated": truncated}

    async def read(self, args):
        async with self.runtime.maker() as session:
            row = await self.owned(session, args.workspace_id)
            if row.status not in ("active", "kept"):
                raise ValueError("Рабочая копия недоступна")
            root = paths(self.root, self.runtime.project_id, row.id)[2]
            return await run_in_threadpool(files.read_file, root, args.path)

    async def publish(self, args):
        if self.git is None:
            raise ValueError("Git пользователя недоступен")
        async with self.runtime.maker() as session:
            row = await self.owned(session, args.workspace_id)
            if row.status not in ("active", "kept"):
                raise ValueError("Рабочая копия недоступна")
            root = str(paths(self.root, self.runtime.project_id, row.id)[2])
            remote = (await gitops.status(self.root)).get("remote")
            if not remote:
                raise ValueError("Настройте HTTPS-репозиторий исходного проекта в разделе Git")
            await gitops.set_remote(root, gitops.validate_remote(remote))
            state = await gitops.status(root)
            if state.get("branch") != row.branch:
                raise ValueError("Ветка рабочей копии изменилась; публикация остановлена")
            git = agent_git.GitAgent(root, self.git.author, self.git.email, self.git.credential, publish_branch=row.branch)
            result = await git.call("git_push", {"branch": row.branch})
            if "error" not in result:
                row.payload = {**row.payload, "published": True, "remote": remote}
                await session.commit()
            return result

    async def call(self, name, arguments):
        try:
            if name not in {tool["function"]["name"] for tool in self.tools()}:
                raise ValueError("Инструмент рабочих копий недоступен в этом режиме или персоне")
            args = SCHEMAS[name].model_validate(arguments)
            if name == "worktree_list":
                return await self.list()
            method = {"enter_worktree": self.enter, "exit_worktree": self.exit, "resume_worktree": self.resume,
                      "worktree_diff": self.diff, "worktree_read": self.read, "worktree_publish": self.publish}[name]
            return await method(args)
        except (ValueError, OSError, gitops.GitError, sandbox.SandboxError) as exc:
            return {"error": str(exc)[:1500]}
        except IntegrityError:
            return {"error": "Рабочая копия уже переключена параллельным запросом"}
