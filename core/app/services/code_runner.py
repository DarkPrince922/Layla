"""Инструменты агента для запуска кода: run_command (песочница проекта) и run_code (Piston).

Набор инструментов собирается на каждый ход: что доступно сейчас (сервис запущен, какие
языки установлены в Piston), то модель и видит — с этими подробностями в описании.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from app.services import files, sandbox

RUN_TOOLS = frozenset({"run_command", "run_code", "start_preview", "check_preview", "stop_preview"})
PREVIEW_TOOLS = frozenset({"start_preview", "check_preview", "stop_preview"})
# Что запускает команды — в режиме «С подтверждением» ждёт решения пользователя.
NEEDS_APPROVAL = frozenset({"run_command", "run_code", "start_preview"})
# Право роли на запуск кода. shell.local — прежнее имя того же права у встроенных ролей.
RUN_PERMISSIONS = ("code.run", "shell.local")
MODEL_OUTPUT = 8000  # символов вывода, которые видит модель
SHOWN_OUTPUT = 40_000  # символов вывода в карточке чата
LIVE_EVERY = 0.5  # секунд между обновлениями живого вывода

_NETWORK = {
    "proxy": "only package registries (PyPI, npm, Go, crates.io, Maven) — other hosts are blocked",
    "off": "none",
}


class RunCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, max_length=8000)
    timeout: int = Field(default=sandbox.DEFAULT_TIMEOUT, ge=1, le=sandbox.MAX_TIMEOUT)
    stdin: str | None = Field(default=None, max_length=100_000)


class RunCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(min_length=1, max_length=40)
    version: str | None = Field(default=None, max_length=40)
    paths: list[str] = Field(default_factory=list, max_length=50)
    code: str | None = Field(default=None, max_length=200_000)
    stdin: str = Field(default="", max_length=100_000)
    args: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _source(self) -> RunCode:
        if not self.paths and not (self.code or "").strip():
            raise ValueError("нужны paths (файлы проекта) или code")
        return self


class StartPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str | None = Field(default=None, max_length=4000)


class CheckPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default="/", max_length=2000)


class StopPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")


_SCHEMAS = {"run_command": RunCommand, "run_code": RunCode, "start_preview": StartPreview,
            "check_preview": CheckPreview, "stop_preview": StopPreview}


def can_run(permissions: list[str] | None) -> bool:
    return permissions is None or any(p in permissions for p in RUN_PERMISSIONS)


def _spec(name: str, description: str, schema: type[BaseModel]) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": description, "parameters": schema.model_json_schema()}}


def command_tool(box: dict) -> dict:
    tools = box.get("tools") or {}
    listed = ", ".join(f"{name} ({version})" for name, version in tools.items()) or "bash"
    network = _NETWORK.get(box.get("network"), _NETWORK["off"])
    limit = (box.get("limits") or {}).get("max_timeout", sandbox.MAX_TIMEOUT)
    return _spec("run_command", (
        "Run a bash command in this project's sandbox: a working copy of the current project files "
        f"with its own dependencies. Available: {listed}. A Python virtualenv (.venv) is active, so "
        "`pip install -r requirements.txt` works; `npm install` puts node_modules into the copy; "
        "installed dependencies stay between runs. Use it to install dependencies, run tests, "
        "linters, type checks, builds and scripts — and to verify your changes. "
        f"Internet access: {network}. Files the command creates or changes stay in the sandbox and "
        "are NOT saved to the project: change project files only with the file tools (for a new "
        "dependency, add it to requirements.txt / package.json, then install). Non-interactive: no "
        f"TTY, pass input via stdin. Killed at timeout (default 120 s, max {limit} s), so do not "
        "start servers or watchers; run one-shot commands."
    ), RunCommand)


def code_tool(runtimes: list[dict]) -> dict:
    listed = ", ".join(sorted({f"{r['language']} {r.get('version', '')}".strip() for r in runtimes}))
    return _spec("run_code", (
        "Compile and run a standalone program in an isolated runner (Piston) — for languages the "
        f"sandbox does not have. Installed languages: {listed}. Pass project files in paths (the "
        "first one is the entry point) or inline source in code. No network, no third-party "
        "packages, short time limits (seconds): for quick checks of algorithms and small programs."
    ), RunCode)


def preview_tools() -> list[dict]:
    return [
        _spec("start_preview", (
            "Start (or restart) the project's app in the sandbox so the user can see and click it live in "
            "Код → Превью: a dev server (npm run dev, Django runserver, python app.py…) or a static server. "
            "Without command it is chosen from the project (package.json dev/start, manage.py, index.html). "
            "The server should listen on $PORT (env) if it can; any port works. Waits until the app answers "
            "and returns its state, the first page and recent logs. Project file edits reach the running app "
            "automatically (hot reload where the framework supports it)."
        ), StartPreview),
        _spec("check_preview", (
            "Open a page of the running preview from the server side: status code, title, visible text of the "
            "HTML and recent server logs (build errors show up there). JavaScript is not executed, so for SPAs "
            "check the logs and that the page and its assets load."
        ), CheckPreview),
        _spec("stop_preview", "Stop the running preview of this project.", StopPreview),
    ]


def validate(name: str, arguments: dict) -> tuple[BaseModel | None, str | None]:
    schema = _SCHEMAS.get(name, RunCode)
    try:
        return schema.model_validate(arguments), None
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, e["loc"])) or e["msg"] for e in exc.errors())
        return None, f"Некорректные аргументы инструмента: {fields}. Проверьте JSON-схему."


def label(name: str, arguments: dict) -> str:
    """Что показать в карточке и на подтверждении: команда или язык с файлами."""
    if name == "run_command":
        return str(arguments.get("command") or "")[:2000]
    if name == "start_preview":
        return "превью: " + (str(arguments.get("command") or "").strip() or "команда по проекту")[:2000]
    if name == "check_preview":
        return f"проверка превью: {arguments.get('path') or '/'}"
    if name == "stop_preview":
        return "остановить превью"
    paths = arguments.get("paths") if isinstance(arguments.get("paths"), list) else []
    target = ", ".join(str(p) for p in paths[:5]) or "код из сообщения"
    return f"{arguments.get('language', '?')}: {target}"


class Runner:
    """Запуск кода в рамках одного проекта одного пользователя."""

    def __init__(self, owner_id: str, project_id: str, root: str) -> None:
        self.owner_id = owner_id
        self.project_id = project_id
        self.root = root

    async def tools(self) -> list[dict]:
        found = []
        box = await sandbox.info()
        if box is not None:
            found.append(command_tool(box))
            found.extend(preview_tools())
        runtimes = await sandbox.runtimes()
        if runtimes:
            found.append(code_tool(runtimes))
        return found

    async def command(self, args: RunCommand) -> AsyncIterator[dict]:
        """События {"output": весь вывод на сейчас} по ходу и {"result": ...} в конце."""
        chunks: list[str] = []
        exit_event: dict | None = None
        notes: list[str] = []
        shown_at = 0.0
        started = time.monotonic()
        try:
            async for event in sandbox.run(self.owner_id, self.project_id, self.root, args.command,
                                           timeout=args.timeout, stdin=args.stdin):
                kind = event.get("type")
                if kind == "output":
                    chunks.append(event.get("data", ""))
                    if time.monotonic() - shown_at >= LIVE_EVERY:
                        shown_at = time.monotonic()
                        yield {"output": _shown("".join(chunks))}
                elif kind == "info":
                    notes.append(event.get("data", ""))
                    yield {"info": event.get("data", "")}
                elif kind == "exit":
                    exit_event = event
                elif kind == "error":
                    raise sandbox.SandboxError(event.get("data") or "Песочница не выполнила команду")
        except sandbox.SandboxError as exc:
            yield {"result": {"error": str(exc)}}
            return
        except OSError as exc:  # не прочитать файлы проекта для копии
            yield {"result": {"error": f"Не удалось подготовить файлы проекта: {exc.strerror or exc}"}}
            return
        text = sandbox.clean("".join(chunks))
        if exit_event is None:
            yield {"result": {"error": "Песочница не сообщила результат команды", "output": _shown(text)}}
            return
        yield {"result": _command_result(exit_event, text, time.monotonic() - started)}

    async def call(self, name: str, args: BaseModel) -> dict:
        """Инструменты без живого вывода: run_code и превью."""
        if name == "run_code":
            return await self.code(args)
        try:
            if name == "start_preview":
                return await self._start_preview(args)
            if name == "check_preview":
                return _page(await sandbox.preview_fetch(self.owner_id, self.project_id, args.path))
            if name == "stop_preview":
                await sandbox.preview_stop(self.owner_id, self.project_id)
                return {"stopped": True, "shown": "Превью остановлено"}
        except sandbox.SandboxError as exc:
            return {"error": str(exc)}
        return {"error": "Неизвестный инструмент"}

    async def _start_preview(self, args: StartPreview) -> dict:
        await sandbox.preview_start(self.owner_id, self.project_id, self.root, args.command)
        status = await sandbox.preview_wait(self.owner_id, self.project_id)
        logs = _tail(status.get("logs") or "")
        if status.get("state") != "running":
            return {"error": f"Превью не запустилось (состояние: {status.get('state')}). Смотрите логи.",
                    "command": status.get("command"), "logs_tail": logs, "shown": logs}
        page = _page(await sandbox.preview_fetch(self.owner_id, self.project_id, "/"))
        page.pop("shown", None)
        return {"state": "running", "command": status.get("command"), "port": status.get("port"),
                "note": "The user sees the running app in Код → Превью.", "first_page": page,
                "shown": f"Превью работает · порт {status.get('port')}\n{logs}"}

    async def code(self, args: RunCode) -> dict:
        try:
            sources = await run_in_threadpool(self._sources, args)
            data = await sandbox.execute(args.language, args.version, sources, stdin=args.stdin, args=args.args)
        except sandbox.SandboxError as exc:
            return {"error": str(exc)}
        except (FileNotFoundError, ValueError, OSError) as exc:
            return {"error": f"Не удалось прочитать файлы для запуска: {exc}"}
        return _code_result(data)

    def _sources(self, args: RunCode) -> list[dict]:
        sources = []
        for path in args.paths:
            item = files.read_file(self.root, path)
            sources.append({"name": path, "content": item["content"]})
        if args.code and args.code.strip():
            name = "main" + _EXTENSIONS.get(args.language.lower(), ".txt")
            if sources:
                sources.append({"name": name, "content": args.code})
            else:
                sources = [{"name": name, "content": args.code}]
        return sources


_EXTENSIONS = {
    "python": ".py", "py": ".py", "javascript": ".js", "js": ".js", "typescript": ".ts", "ts": ".ts",
    "go": ".go", "rust": ".rs", "c": ".c", "c++": ".cpp", "cpp": ".cpp", "java": ".java",
    "kotlin": ".kt", "csharp": ".cs", "c#": ".cs", "php": ".php", "ruby": ".rb", "swift": ".swift",
    "bash": ".sh", "lua": ".lua", "haskell": ".hs", "dart": ".dart", "scala": ".scala", "zig": ".zig",
}


def _shown(text: str) -> str:
    return sandbox.squeeze(sandbox.clean(text), SHOWN_OUTPUT)[0]


def _command_result(event: dict, text: str, took: float) -> dict:
    model_text, cut = sandbox.squeeze(text, MODEL_OUTPUT)
    result = {
        "exit_code": event.get("code"),
        "duration_s": event.get("duration", round(took, 2)),
        "output": model_text,
    }
    if event.get("signal"):
        result["signal"] = event["signal"]
    if event.get("timed_out"):
        result["timed_out"] = True
        result["note"] = "The command was killed at the timeout."
    if cut or event.get("truncated"):
        result["output_truncated"] = True
    result["shown"] = _shown(text)
    return result


def _stage(stage: dict | None) -> dict | None:
    if not stage:
        return None
    text = sandbox.clean(stage.get("output") or "")
    out = {"exit_code": stage.get("code"), "output": sandbox.squeeze(text, MODEL_OUTPUT // 2)[0]}
    if stage.get("signal"):
        out["signal"] = stage["signal"]
    if stage.get("message"):
        out["message"] = stage["message"]
    return out


def _code_result(data: dict) -> dict:
    result = {"language": data.get("language"), "version": data.get("version")}
    compiled = _stage(data.get("compile"))
    if compiled is not None:
        result["compile"] = compiled
    ran = _stage(data.get("run"))
    if ran is not None:
        result.update(ran)
    shown = []
    if compiled is not None and (compiled["output"] or compiled["exit_code"]):
        shown.append(f"[compile · exit {compiled['exit_code']}]\n{compiled['output']}")
    if ran is not None:
        shown.append(ran["output"])
    result["shown"] = "\n".join(shown)
    return result


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(sandbox.clean(text).splitlines()[-lines:])


def _page(data: dict) -> dict:
    """Страница превью глазами агента + текст для карточки."""
    if data.get("state") != "running":
        return {"error": f"Превью не работает (состояние: {data.get('state')})",
                "logs_tail": _tail(data.get("logs_tail") or ""), "shown": _tail(data.get("logs_tail") or "")}
    result = {k: data.get(k) for k in ("status", "content_type", "title", "text", "error") if data.get(k) is not None}
    result["logs_tail"] = _tail(data.get("logs_tail") or "", 25)
    status = data.get("status")
    result["shown"] = (f"HTTP {status} · {data.get('title') or 'без заголовка'}\n{(data.get('text') or '')[:600]}"
                       if status else str(data.get("error") or ""))
    return result
