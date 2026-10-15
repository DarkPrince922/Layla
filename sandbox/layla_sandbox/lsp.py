"""Bounded stdio LSP sessions. Only image-owned servers run, always as workspace uid.

The root HTTP service never opens a project file: text comes from Core's no-follow
reader. Language servers read their workspace under the same uid as commands.
No arbitrary method, executable, environment or workspace edit is exposed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from contextlib import suppress
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from layla_sandbox.workspace import Workspace, check_path, kill_session

MAX_MESSAGE = 4_000_000
MAX_RESULT = 100_000
MAX_ITEMS = 100
TIMEOUT = 30
IDLE = 600
LIFETIME = 3600
MAX_SERVERS = 16
MAX_PER_USER = 8
COMMANDS = {
    "python": ["/opt/layla-lsp/python/bin/pylsp"],
    "typescript": ["/opt/layla-lsp/node_modules/.bin/typescript-language-server", "--stdio"],
}
EXTENSIONS = {".py": ("python", "python"), ".pyi": ("python", "python"),
              ".js": ("typescript", "javascript"), ".jsx": ("typescript", "javascriptreact"),
              ".ts": ("typescript", "typescript"), ".tsx": ("typescript", "typescriptreact"),
              ".mjs": ("typescript", "javascript"), ".cjs": ("typescript", "javascript"),
              ".mts": ("typescript", "typescript"), ".cts": ("typescript", "typescript")}
METHODS = {
    "goToDefinition": ("textDocument/definition", "definitionProvider"),
    "findReferences": ("textDocument/references", "referencesProvider"),
    "hover": ("textDocument/hover", "hoverProvider"),
    "documentSymbol": ("textDocument/documentSymbol", "documentSymbolProvider"),
    "workspaceSymbol": ("workspace/symbol", "workspaceSymbolProvider"),
    "goToImplementation": ("textDocument/implementation", "implementationProvider"),
    "prepareCallHierarchy": ("textDocument/prepareCallHierarchy", "callHierarchyProvider"),
    "incomingCalls": ("textDocument/prepareCallHierarchy", "callHierarchyProvider"),
    "outgoingCalls": ("textDocument/prepareCallHierarchy", "callHierarchyProvider"),
}
SETTINGS = {"pylsp": {"plugins": {"pycodestyle": {"enabled": False},
                                   "pyflakes": {"enabled": True}}}}


class LspError(ValueError):
    pass


def python_diagnostic(item: dict, text: str) -> dict:
    # Pinned pylsp's Pyflakes adapter mixes AST byte columns for flake.start
    # with Python character columns for flake.end and SyntaxError offsets.
    # Convert those known wire shapes to codepoints before Core's UTF-16 pass.
    if item.get("source") != "pyflakes":
        return item
    span = item.get("range", {})
    start, end = span.get("start", {}), span.get("end", {})
    lines = text.splitlines(keepends=True)
    line, column = start.get("line", -1), start.get("character", 0)
    if not 0 <= line < len(lines) or end.get("line") != line:
        return item
    if end.get("character") == len(lines[line]):
        column = len(lines[line].encode()[:column].decode(errors="ignore"))
    elif end.get("character", 0) > len(lines[line]):
        column = max(0, column - 1)  # SyntaxError.offset is 1-based
    else:
        return item
    return {**item, "range": {"start": {**start, "character": column},
                             "end": {**end, "character": len(lines[line].rstrip("\r\n"))}}}


def available() -> dict:
    return {name: {"extensions": [ext for ext, pair in EXTENSIONS.items() if pair[0] == name]}
            for name, argv in COMMANDS.items() if os.access(argv[0], os.X_OK)}


def normalize(value, root: Path, budget: list[int], depth=0):
    """Discard external locations and bound nested server-controlled results."""
    if depth > 20 or budget[0] <= 0:
        return None
    budget[0] -= 1
    if isinstance(value, list):
        return [item for entry in value[:MAX_ITEMS]
                if (item := normalize(entry, root, budget, depth + 1)) is not None]
    if isinstance(value, dict):
        result = {}
        for key, entry in list(value.items())[:50]:
            if key in ("uri", "targetUri"):
                try:
                    url = urlsplit(entry)
                    if url.scheme != "file" or url.netloc or url.query or url.fragment:
                        return None
                    rel = str(PurePosixPath(unquote(url.path)).relative_to(root))
                    check_path(rel)
                    if any(part in {".git", ".venv", "node_modules"} for part in rel.split("/")):
                        return None
                except (ValueError, TypeError):
                    return None
                result["path"] = rel
            else:
                result[key] = normalize(entry, root, budget, depth + 1)
        return result
    if isinstance(value, str):
        return value[:16000]
    return value if value is None or isinstance(value, (bool, int, float)) else None


class Session:
    def __init__(self, ws: Workspace, language: str):
        self.ws, self.language = ws, language
        self.proc = None
        self.tasks: list[asyncio.Task] = []
        self.pending: dict[int, asyncio.Future] = {}
        self.sequence = 0
        self.writes = asyncio.Lock()
        self.capabilities = {}
        self.documents: dict[str, tuple[int, str]] = {}
        self.diagnostics: dict[str, dict] = {}
        self.signals: dict[str, asyncio.Event] = {}
        self.created = self.touched = time.monotonic()
        self.closed = False
        # The pinned pylsp 1.12 uses Python string indices internally. Keep its
        # legacy coordinate convention explicit; Core converts result ranges.
        self.encoding = "utf-32" if language == "python" else "utf-16"

    @property
    def alive(self):
        return not self.closed and self.proc is not None and self.proc.returncode is None

    async def send(self, message: dict):
        if not self.alive:
            raise LspError("Language server остановлен; повторите запрос для перезапуска")
        data = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False).encode()
        if len(data) > MAX_MESSAGE:
            raise LspError("Сообщение LSP слишком большое")
        async with self.writes:
            self.proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
            await self.proc.stdin.drain()

    async def ask(self, method: str, params=None, timeout=TIMEOUT):
        self.touched = time.monotonic()
        self.sequence += 1
        ident = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            await self.send({"id": ident, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except (TimeoutError, asyncio.CancelledError):
            with suppress(Exception):
                await self.send({"method": "$/cancelRequest", "params": {"id": ident}})
            raise
        finally:
            self.pending.pop(ident, None)

    async def notify(self, method, params):
        await self.send({"method": method, "params": params})

    async def start(self):
        argv = COMMANDS[self.language]
        if not os.access(argv[0], os.X_OK):
            raise LspError("Language server не установлен в образе sandbox; пересоберите sandbox")
        self.proc = await asyncio.create_subprocess_exec(
            *self.ws._wrap(argv), cwd=str(self.ws.path), env=self.ws.env(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=MAX_MESSAGE + 8192,
            **self.ws._as_user(),
        )
        self.tasks = [asyncio.create_task(self.read()), asyncio.create_task(self.drain_errors())]
        try:
            result = await self.ask("initialize", {
                "processId": None, "rootUri": self.ws.path.as_uri(),
                "workspaceFolders": [{"uri": self.ws.path.as_uri(), "name": self.ws.project}],
                "capabilities": {"general": {"positionEncodings": ["utf-16"]},
                                 "workspace": {"configuration": True, "workspaceFolders": True,
                                               "applyEdit": False},
                                 "textDocument": {"publishDiagnostics": {"versionSupport": True},
                                                  "callHierarchy": {"dynamicRegistration": False},
                                                  "documentSymbol": {"hierarchicalDocumentSymbolSupport": True}}},
            })
            self.capabilities = result.get("capabilities", {})
            if self.capabilities.get("positionEncoding", "utf-16") != "utf-16":
                raise LspError("Language server выбрал неподдерживаемую кодировку позиций")
            await self.notify("initialized", {})
            await self.notify("workspace/didChangeConfiguration", {"settings": SETTINGS})
        except BaseException:
            await self.close()
            raise

    async def drain_errors(self):
        # Keep stderr separate from framing, drain without retaining project text.
        while await self.proc.stderr.read(8192):
            pass

    async def read(self):
        try:
            while True:
                header = await self.proc.stdout.readuntil(b"\r\n\r\n")
                if len(header) > 8192:
                    raise LspError("Некорректный заголовок LSP")
                fields = dict(line.split(b":", 1) for line in header.strip().split(b"\r\n"))
                length = int(next(v for k, v in fields.items() if k.lower() == b"content-length"))
                if not 0 < length <= MAX_MESSAGE:
                    raise LspError("Некорректный размер LSP ответа")
                message = json.loads(await self.proc.stdout.readexactly(length))
                if not isinstance(message, dict):
                    raise LspError("Некорректный JSON-RPC ответ")
                if "method" in message:
                    await self.inbound(message)
                elif (future := self.pending.get(message.get("id"))) is not None and not future.done():
                    if "error" in message:
                        error = message["error"]
                        future.set_exception(LspError("LSP: " + str(error.get("message", "ошибка"))[:500]))
                    else:
                        future.set_result(message.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a malformed/crashed subprocess must release every waiter
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(LspError("Связь с language server прервана; повторите запрос"))
            self.closed = True
            for event in self.signals.values():
                event.set()
            if self.proc.returncode is None:
                self.kill()

    async def inbound(self, message):
        method, params = message["method"], message.get("params") or {}
        if "id" in message:
            if method == "workspace/configuration":
                result = []
                for item in params.get("items", [])[:100]:
                    setting = SETTINGS
                    for part in (item.get("section") or "").split("."):
                        setting = setting.get(part, {}) if part and isinstance(setting, dict) else setting
                    result.append(setting)
            elif method == "workspace/workspaceFolders":
                result = [{"uri": self.ws.path.as_uri(), "name": self.ws.project}]
            elif method == "workspace/applyEdit":
                result = {"applied": False, "failureReason": "LSP is read-only; use Code file tools"}
            elif method in {"client/registerCapability", "client/unregisterCapability",
                            "window/workDoneProgress/create", "workspace/diagnostic/refresh"}:
                result = None
            else:
                await self.send({"id": message["id"], "error": {"code": -32601, "message": "Unsupported client request"}})
                return
            await self.send({"id": message["id"], "result": result})
        elif method == "textDocument/publishDiagnostics":
            uri = params.get("uri")
            document = self.documents.get(uri)
            if document is not None and params.get("version", document[0]) == document[0]:
                self.diagnostics[uri] = {"items": params.get("diagnostics", [])[:MAX_ITEMS],
                                         "version": document[0], "ready": True}
                self.signals[uri].set()

    async def open(self, path, text, language_id):
        uri = (self.ws.path / path).as_uri()
        digest = hashlib.sha256(text.encode()).hexdigest()
        old = self.documents.get(uri)
        if old and old[1] == digest:
            return uri
        if not old and len(self.documents) >= 64:
            await self.close_document(next(iter(self.documents)))
        version = old[0] + 1 if old else 1
        self.documents[uri] = version, digest
        self.diagnostics.pop(uri, None)
        self.signals[uri] = asyncio.Event()
        if old:
            await self.notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": version},
                                                        "contentChanges": [{"text": text}]})
        else:
            await self.notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": language_id,
                                                                        "version": version, "text": text}})
        return uri

    async def close_document(self, uri):
        self.documents.pop(uri, None)
        self.diagnostics.pop(uri, None)
        self.signals.pop(uri, None)
        await self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    async def query(self, body):
        self.touched = time.monotonic()
        language_id = EXTENSIONS[Path(body.path).suffix.lower()][1]
        uri = await self.open(body.path, body.text, language_id)
        if body.operation == "diagnostics":
            if self.capabilities.get("diagnosticProvider") not in (None, False):
                result = await self.ask("textDocument/diagnostic", {"textDocument": {"uri": uri}})
                result = {"ready": True, "items": result.get("items", []), "version": self.documents[uri][0]}
            else:
                if uri not in self.diagnostics:
                    await self.notify("textDocument/didSave", {"textDocument": {"uri": uri}})
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self.signals[uri].wait(), 5)
                if not self.alive:
                    raise LspError("Language server прервал анализ")
                result = self.diagnostics.get(uri, {"ready": False, "items": [], "version": self.documents[uri][0]})
            if self.language == "python":
                result = {**result, "items": [python_diagnostic(item, body.text) for item in result.get("items", [])]}
        else:
            method, capability = METHODS[body.operation]
            if self.capabilities.get(capability) in (None, False):
                raise LspError(f"{self.language}: сервер не поддерживает {body.operation}")
            character = body.character - 1
            if self.encoding == "utf-32":
                lines = body.text.split("\n")
                if body.line > len(lines):
                    raise LspError("Позиция находится за пределами файла")
                character = len(lines[body.line - 1].encode("utf-16-le")[:character * 2].decode("utf-16-le", errors="ignore"))
            params = {"textDocument": {"uri": uri}, "position": {"line": body.line - 1, "character": character}}
            if body.operation == "workspaceSymbol":
                params = {"query": body.query}
            elif body.operation == "documentSymbol":
                params.pop("position")
            elif body.operation == "findReferences":
                params["context"] = {"includeDeclaration": True}
            result = await self.ask(method, params)
            if body.operation in {"incomingCalls", "outgoingCalls"}:
                result = await self.ask("callHierarchy/" + body.operation, {"item": result[0]}) if result else []
        budget = [5000]
        normalized = normalize(result, self.ws.path, budget)
        encoded = json.dumps(normalized, ensure_ascii=False)
        if len(encoded) > MAX_RESULT:
            raise LspError("Результат LSP слишком большой; уточните запрос")
        return {"operation": body.operation, "path": body.path, "language": self.language,
                "result": normalized, "position_encoding": self.encoding, "ranges": "zero-based",
                "bounded": True}

    async def close(self):
        if self.alive:
            with suppress(Exception):
                await self.ask("shutdown", None, timeout=1)
                await self.notify("exit", None)
        self.closed = True
        if self.proc is not None:
            self.kill()
            with suppress(Exception):
                await asyncio.wait_for(self.proc.wait(), 3)
            self.proc.stdin.close()
            with suppress(Exception):
                await self.proc.stdin.wait_closed()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for future in self.pending.values():
            if not future.done():
                future.cancel()

    def kill(self):
        # killpg already stops descendants; /proc scanning is an extra cleanup
        # and may be unavailable on a restricted developer/test host.
        with suppress(OSError):
            kill_session(self.proc.pid)
        if self.proc.returncode is None:
            with suppress(ProcessLookupError):
                self.proc.kill()


class Registry:
    def __init__(self):
        self.items: dict[tuple, Session] = {}
        self.lock = asyncio.Lock()

    def active_for(self, uid):
        return any(key[0] == uid and session.alive for key, session in self.items.items())

    async def query(self, ws, body):
        check_path(body.path)
        pair = EXTENSIONS.get(Path(body.path).suffix.lower())
        if pair is None:
            raise LspError("Для этого расширения language server не настроен")
        key = ws.uid, ws.project, pair[0]
        async with self.lock:
            session = self.items.get(key)
            if session is not None and not session.alive:
                await session.close()
                self.items.pop(key, None)
                session = None
            if session is None:
                if len(self.items) >= MAX_SERVERS or sum(k[0] == ws.uid for k in self.items) >= MAX_PER_USER:
                    raise LspError("Достигнут лимит LSP серверов; остановите ненужные сессии")
                session = Session(ws, pair[0])
                await session.start()
                self.items[key] = session
        try:
            return await session.query(body)
        except (TimeoutError, asyncio.CancelledError):
            await session.close()
            self.items.pop(key, None)
            raise

    async def changed(self, uid, project, names, removed):
        for key, session in list(self.items.items()):
            if key[:2] != (uid, project) or not session.alive:
                continue
            try:
                # A dependency change invalidates diagnostics of its importers
                # too; absence of a new notification must not mean 'clean'.
                if names or removed:
                    session.diagnostics.clear()
                    session.signals = {uri: asyncio.Event() for uri in session.documents}
                for rel in names | set(removed):
                    uri = (session.ws.path / rel).as_uri()
                    if uri in session.documents:
                        await session.close_document(uri)
                changes = [{"uri": (session.ws.path / rel).as_uri(), "type": 3 if rel in removed else 2}
                           for rel in names | set(removed)]
                if changes:
                    await session.notify("workspace/didChangeWatchedFiles", {"changes": changes})
            except (LspError, OSError):
                await session.close()
                self.items.pop(key, None)

    def status(self, ws):
        return {"available": available(), "sessions": [{"language": key[2], "running": item.alive,
                "documents": len(item.documents)} for key, item in self.items.items() if key[:2] == (ws.uid, ws.project)]}

    async def stop(self, uid, project):
        for key in list(self.items):
            if key[:2] == (uid, project):
                await self.items.pop(key).close()

    async def reap(self):
        at = time.monotonic()
        for key, session in list(self.items.items()):
            if not session.alive or at - session.touched > IDLE or at - session.created > LIFETIME:
                self.items.pop(key, None)
                await session.close()

    async def shutdown(self):
        for session in list(self.items.values()):
            await session.close()
        self.items.clear()
