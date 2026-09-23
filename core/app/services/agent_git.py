"""Инструменты агента для Git: статус, история, дифф, коммит и пуш.

Коммит меняет только историю проекта, файлы не трогает — в режимах «План» и «Ревью» его
нет, в режиме «С подтверждением» он ждёт решения. Пуш уходит наружу, поэтому ждёт
подтверждения пользователя всегда, в любом режиме.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services import gitops

GIT_TOOLS = frozenset({"git_status", "git_log", "git_diff", "git_commit", "git_push"})
GIT_MUTATING = frozenset({"git_commit", "git_push"})
ALWAYS_ASK = frozenset({"git_push"})
GIT_PERMISSION = "repo.git"
MODEL_DIFF = 12_000


class _NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitLog(_NoArgs):
    limit: int = Field(default=10, ge=1, le=50)


class GitCommit(_NoArgs):
    message: str = Field(min_length=1, max_length=2000)


class GitPush(_NoArgs):
    branch: str | None = Field(default=None, max_length=200)


_SCHEMAS = {"git_status": _NoArgs, "git_log": GitLog, "git_diff": _NoArgs, "git_commit": GitCommit,
            "git_push": GitPush}
_DESCRIPTIONS = {
    "git_status": "Git state of this project: branch, uncommitted changes, remote, ahead/behind, last commit.",
    "git_log": "Recent commits of this project (newest first).",
    "git_diff": "Uncommitted changes of this project as a unified diff (new files included).",
    "git_commit": "Commit all current project changes with a clear message (initialises git if needed). "
                  "Use it when the user asks to commit or after finishing a requested change set.",
    "git_push": "Push the committed history to the project's remote repository. The user always has to "
                "approve a push; only push when the user asked for it.",
}


def can_use(permissions: list[str] | None) -> bool:
    return permissions is None or GIT_PERMISSION in permissions


def specs(mode: str) -> list[dict]:
    names = [n for n in _SCHEMAS if not (mode in ("plan", "review") and n in GIT_MUTATING)]
    return [{"type": "function", "function": {"name": n, "description": _DESCRIPTIONS[n],
                                              "parameters": _SCHEMAS[n].model_json_schema()}} for n in names]


def label(name: str, arguments: dict) -> str:
    if name == "git_commit":
        return f"git commit — {str(arguments.get('message') or '')[:200]}"
    if name == "git_push":
        return f"git push {arguments.get('branch') or ''}".strip()
    return name.replace("_", " ")


class GitAgent:
    """Git одного проекта от имени пользователя; токен хостинга — через credential()."""

    def __init__(self, root: str, author: str, email: str,
                 credential: Callable[[str | None], Awaitable[tuple[str | None, str | None]]]) -> None:
        self.root = root
        self.author = author
        self.email = email
        self.credential = credential

    async def call(self, name: str, arguments: dict) -> dict:
        schema = _SCHEMAS.get(name)
        if schema is None:
            return {"error": "Неизвестный инструмент"}
        try:
            args = schema.model_validate(arguments or {})
        except ValidationError as exc:
            fields = ", ".join(".".join(map(str, e["loc"])) or e["msg"] for e in exc.errors())
            return {"error": f"Некорректные аргументы инструмента: {fields}"}
        try:
            if name == "git_status":
                state = await gitops.status(self.root)
                if state.get("initialized"):
                    state["changes"] = state["changes"][:100]
                return state
            if name == "git_log":
                return {"commits": await gitops.log(self.root, args.limit)}
            if name == "git_diff":
                data = await gitops.diff(self.root)
                text = data["diff"]
                cut = len(text) > MODEL_DIFF
                return {"diff": text[:MODEL_DIFF] + ("\n…[дифф обрезан]" if cut else ""),
                        "truncated": cut or data["truncated"]}
            if name == "git_commit":
                done = await gitops.commit(self.root, args.message, self.author, self.email)
                return {"commit": done}
            if name == "git_push":
                remote = (await gitops.status(self.root)).get("remote")
                username, token = await self.credential(remote)
                done = await gitops.push(self.root, token, username, args.branch)
                return {"branch": done["branch"], "output": done["output"][-1500:]}
        except gitops.GitError as exc:
            return {"error": str(exc)}
        return {"error": "Неизвестный инструмент"}
