"""Git для проектов «Кода»: статус, коммит, история, пуш и пулл, токены хостингов."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import _owned_project, _project_root
from app.db import get_session
from app.models.git import GitCredential
from app.models.user import User
from app.security import crypto
from app.services import audit, gitops
from app.services.auth import get_current_user

router = APIRouter(tags=["git"])


def _fail(exc: gitops.GitError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


async def _root(session: AsyncSession, user: User, project_id: str) -> str:
    return _project_root(await _owned_project(session, user, project_id))


async def credential(session: AsyncSession, owner_id: str, url: str | None) -> tuple[str | None, str | None]:
    """(логин, токен) для хоста удалённого репозитория или (None, None)."""
    host = gitops.host_of(url or "")
    if not host:
        return None, None
    row = await session.scalar(select(GitCredential).where(
        GitCredential.owner_id == owner_id, GitCredential.host == host))
    if row is None:
        return None, None
    return row.username, crypto.decrypt(row.secret_ref)


async def _state(session: AsyncSession, user: User, root: str) -> dict:
    state = await gitops.status(root)
    remote = state.get("remote")
    state["remote_host"] = gitops.host_of(remote) if remote else None
    state["has_token"] = bool(remote) and (await credential(session, user.id, remote))[1] is not None
    return state


@router.get("/projects/{project_id}/git")
async def git_status(project_id: str, user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)) -> dict:
    root = await _root(session, user, project_id)
    try:
        return await _state(session, user, root)
    except gitops.GitError as exc:
        raise _fail(exc) from exc


@router.post("/projects/{project_id}/git/init")
async def git_init(project_id: str, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> dict:
    root = await _root(session, user, project_id)
    try:
        await gitops.init(root)
    except gitops.GitError as exc:
        raise _fail(exc) from exc
    await audit.record(session, actor=user.id, action="project.git.init", target=project_id)
    await session.commit()
    return await _state(session, user, root)


class CommitIn(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


@router.post("/projects/{project_id}/git/commit")
async def git_commit(project_id: str, body: CommitIn, user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)) -> dict:
    root = await _root(session, user, project_id)
    try:
        done = await gitops.commit(root, body.message, user.display_name or user.email.split("@")[0], user.email)
    except gitops.GitError as exc:
        raise _fail(exc) from exc
    await audit.record(session, actor=user.id, action="project.git.commit", target=project_id,
                       meta={"sha": done["sha"], "message": body.message[:200]})
    await session.commit()
    return {"commit": done, "status": await _state(session, user, root)}


@router.get("/projects/{project_id}/git/log")
async def git_log(project_id: str, limit: int = Query(default=50, ge=1, le=200),
                  user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[dict]:
    root = await _root(session, user, project_id)
    try:
        return await gitops.log(root, limit)
    except gitops.GitError as exc:
        raise _fail(exc) from exc


@router.get("/projects/{project_id}/git/diff")
async def git_diff(project_id: str, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> dict:
    root = await _root(session, user, project_id)
    try:
        return await gitops.diff(root)
    except gitops.GitError as exc:
        raise _fail(exc) from exc


@router.get("/projects/{project_id}/git/commits/{sha}")
async def git_show(project_id: str, sha: str, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> dict:
    root = await _root(session, user, project_id)
    try:
        return await gitops.show(root, sha)
    except gitops.GitError as exc:
        raise _fail(exc) from exc


class RemoteIn(BaseModel):
    url: str = Field(min_length=8, max_length=1000)


@router.put("/projects/{project_id}/git/remote")
async def git_remote(project_id: str, body: RemoteIn, user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)) -> dict:
    root = await _root(session, user, project_id)
    try:
        if not gitops.is_repo(root):
            await gitops.init(root)
        await gitops.set_remote(root, body.url)
    except gitops.GitError as exc:
        raise _fail(exc) from exc
    await audit.record(session, actor=user.id, action="project.git.remote", target=project_id,
                       meta={"url": gitops.clean_url(body.url)})
    await session.commit()
    return await _state(session, user, root)


class PushIn(BaseModel):
    branch: str | None = Field(default=None, max_length=200)


@router.post("/projects/{project_id}/git/push")
async def git_push(project_id: str, body: PushIn | None = None, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> dict:
    root = await _root(session, user, project_id)
    try:
        remote = (await gitops.status(root)).get("remote")
        username, token = await credential(session, user.id, remote)
        done = await gitops.push(root, token, username, (body.branch if body else None))
    except gitops.GitError as exc:
        raise _fail(exc) from exc
    await audit.record(session, actor=user.id, action="project.git.push", target=project_id,
                       meta={"branch": done["branch"], "remote": remote})
    await session.commit()
    return {**done, "status": await _state(session, user, root)}


@router.post("/projects/{project_id}/git/pull")
async def git_pull(project_id: str, user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> dict:
    root = await _root(session, user, project_id)
    try:
        remote = (await gitops.status(root)).get("remote")
        username, token = await credential(session, user.id, remote)
        done = await gitops.pull(root, token, username)
    except gitops.GitError as exc:
        raise _fail(exc) from exc
    await audit.record(session, actor=user.id, action="project.git.pull", target=project_id)
    await session.commit()
    return {**done, "status": await _state(session, user, root)}


# --- токены хостингов ----------------------------------------------------------

class CredentialIn(BaseModel):
    host: str = Field(min_length=3, max_length=255, pattern=r"^[A-Za-z0-9.-]+(:\d+)?$")
    username: str | None = Field(default=None, max_length=255)
    token: str = Field(min_length=4, max_length=1000)


def _public(row: GitCredential, token: str | None = None) -> dict:
    return {"host": row.host, "username": row.username,
            "masked": crypto.mask(token if token is not None else crypto.decrypt(row.secret_ref))}


@router.get("/git/credentials")
async def list_credentials(user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await session.scalars(select(GitCredential).where(GitCredential.owner_id == user.id)
                                 .order_by(GitCredential.host))
    return [_public(row) for row in rows]


@router.put("/git/credentials")
async def set_credential(body: CredentialIn, user: User = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)) -> dict:
    host = body.host.lower().split(":")[0]
    row = await session.scalar(select(GitCredential).where(
        GitCredential.owner_id == user.id, GitCredential.host == host))
    token = body.token.strip()
    if row is None:
        row = GitCredential(owner_id=user.id, host=host)
        session.add(row)
    row.username = (body.username or "").strip() or None
    row.secret_ref = crypto.encrypt(token)
    await audit.record(session, actor=user.id, action="git.credential.set", target=host)
    await session.commit()
    return _public(row, token)


@router.delete("/git/credentials/{host}", status_code=204)
async def delete_credential(host: str, user: User = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)) -> None:
    row = await session.scalar(select(GitCredential).where(
        GitCredential.owner_id == user.id, GitCredential.host == host.lower()))
    if row is None:
        raise HTTPException(status_code=404, detail="Токен для этого хоста не найден")
    await session.delete(row)
    await audit.record(session, actor=user.id, action="git.credential.delete", target=host)
    await session.commit()
