"""Queue Code-only worktree deletion after a successful trash transaction."""
from sqlalchemy import select

from app.models.code_worker import CodeWorker
from app.models.code_workspace import CodeWorkspace
from app.models.enums import Domain
from app.models.user import Project
from app.services import code_workspace, code_worktrees


async def queue(session, chat):
    if chat.domain != Domain.code:
        return
    rows = list(await session.scalars(select(CodeWorker).where(CodeWorker.chat_id == chat.id)))
    for row in rows:
        if row.payload.get("workspace_id"):
            continue  # nested worker storage is deleted with its lead workspace below
        project = await session.get(Project, row.project_id)
        if project is not None and project.path:
            location = code_worktrees.paths(project.path, project.id, row.id)[0]
            session.info.setdefault("code_worker_cleanup", []).append(location)
    workspaces = list(await session.scalars(select(CodeWorkspace).where(CodeWorkspace.chat_id == chat.id)))
    for row in workspaces:
        location = code_workspace.paths(row.source_root, row.project_id, row.id)[0]
        session.info.setdefault("code_worker_cleanup", []).append(location)


def take(session):
    return session.info.pop("code_worker_cleanup", [])
