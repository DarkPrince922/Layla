"""Git для проектов: статус, коммит, история, пуш и пулл.

Команды выполняются в каталоге проекта на сервере Layla. Хуки и fsmonitor отключены,
системный и пользовательский конфиги не читаются, запросов пароля нет. Токен доступа
передаётся только через окружение процесса (не в аргументах и не в .git/config) и
вычищается из вывода.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import tempfile
from urllib.parse import urlparse

MAX_DIFF = 400_000
_SHA = re.compile(r"^[0-9a-f]{4,40}$")
_BRANCH = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,200}(?<![./])$")
_FIELD = "\x1f"
_RECORD = "\x1e"


class GitError(RuntimeError):
    """Ошибка git — текст для пользователя и модели."""


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    home = tempfile.gettempdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    config = {
        "core.hooksPath": os.devnull,
        "core.fsmonitor": "false",
        "core.quotepath": "false",
        "color.ui": "false",
        "safe.directory": "*",
        "protocol.file.allow": "never",
        "protocol.ext.allow": "never",
        **(extra or {}),
    }
    env["GIT_CONFIG_COUNT"] = str(len(config))
    for index, (key, value) in enumerate(config.items()):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


async def _git(root: str, *args: str, config: dict[str, str] | None = None, timeout: float = 60,
               secret: str | None = None, check: bool = True) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=root, env=_env(config),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as exc:
        raise GitError("git не установлен на сервере") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        proc.kill()
        raise GitError(f"git {args[0]}: превышено время ожидания") from exc
    stdout, stderr = out.decode(errors="replace"), err.decode(errors="replace")
    if secret:
        stdout, stderr = stdout.replace(secret, "***"), stderr.replace(secret, "***")
    if check and proc.returncode != 0:
        message = (stderr.strip() or stdout.strip()).splitlines()
        raise GitError(f"git {args[0]}: " + (" ".join(message[-3:]) or "ошибка")[:600])
    return proc.returncode, stdout, stderr


def is_repo(root: str) -> bool:
    return os.path.isdir(os.path.join(root, ".git"))


def _require(root: str) -> None:
    if not is_repo(root):
        raise GitError("Проект ещё не под Git: включите Git для проекта (Код → Git).")


def clean_url(url: str) -> str:
    """URL удалённого репозитория без логина и пароля (их не показываем и не храним)."""
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        parsed = parsed._replace(netloc=netloc)
    return parsed.geturl()


def validate_remote(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise GitError("Нужен https-адрес репозитория, например https://github.com/user/repo.git")
    if parsed.username or parsed.password:
        raise GitError("Не указывайте логин и токен в адресе — токен задаётся отдельно и хранится зашифрованным.")
    return url


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _auth(url: str, username: str | None, token: str) -> dict[str, str]:
    """Заголовок авторизации для одного адреса — только в окружении процесса."""
    host = host_of(url)
    if not username:
        username = "oauth2" if "gitlab" in host else "x-access-token"
    basic = base64.b64encode(f"{username}:{token}".encode()).decode()
    parsed = urlparse(url)
    prefix = f"{parsed.scheme}://{parsed.netloc}/"
    return {f"http.{prefix}.extraheader": f"Authorization: Basic {basic}"}


# --- чтение -------------------------------------------------------------------

async def status(root: str) -> dict:
    if not is_repo(root):
        return {"initialized": False}
    _, out, _ = await _git(root, "status", "--porcelain=v1", "--branch", "-z", "--untracked-files=all")
    items = out.split("\0")
    head = items[0] if items and items[0].startswith("## ") else ""
    branch, ahead, behind, upstream = "", 0, 0, None
    if head:
        text = head[3:]
        info = re.search(r"\[(.*)\]$", text)
        if info:
            for part in info.group(1).split(", "):
                if part.startswith("ahead "):
                    ahead = int(part.split()[1])
                elif part.startswith("behind "):
                    behind = int(part.split()[1])
            text = text[: info.start()].strip()
        if text.startswith("No commits yet on "):
            branch = text[len("No commits yet on "):]
        else:
            branch, _, upstream = text.partition("...")
            upstream = upstream or None
    changes = []
    entries = items[1:] if head else items
    skip = False
    for entry in entries:
        if skip:
            skip = False
            continue
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":
            skip = True  # следующий элемент — старое имя
        changes.append({"path": path, "status": _status_name(code), "code": code.strip()})
    _, remote, _ = await _git(root, "remote", "get-url", "origin", check=False)
    remote = remote.strip()
    return {
        "initialized": True,
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "changes": changes,
        "remote": clean_url(remote) if remote else None,
        "last_commit": (await log(root, 1) or [None])[0],
    }


def _status_name(code: str) -> str:
    if code == "??":
        return "new"
    if "D" in code:
        return "deleted"
    if "R" in code:
        return "renamed"
    if "A" in code:
        return "added"
    return "modified"


async def log(root: str, limit: int = 30) -> list[dict]:
    _require(root)
    code, out, _ = await _git(
        root, "log", f"-n{max(1, min(limit, 200))}",
        f"--pretty=format:%H{_FIELD}%h{_FIELD}%an{_FIELD}%ae{_FIELD}%aI{_FIELD}%s{_RECORD}", check=False)
    if code != 0:  # ещё нет коммитов
        return []
    commits = []
    for record in out.split(_RECORD):
        fields = record.strip("\n").split(_FIELD)
        if len(fields) == 6:
            sha, short, name, email, date, subject = fields
            commits.append({"sha": sha, "short": short, "author": name, "email": email,
                            "date": date, "message": subject})
    return commits


def _cut(text: str) -> tuple[str, bool]:
    return (text[:MAX_DIFF], True) if len(text) > MAX_DIFF else (text, False)


async def diff(root: str) -> dict:
    """Незакоммиченные изменения, включая новые файлы (индекс git не трогаем)."""
    _require(root)
    has_head = (await _git(root, "rev-parse", "--verify", "-q", "HEAD", check=False))[0] == 0
    parts = []
    if has_head:
        parts.append((await _git(root, "diff", "HEAD", "--no-ext-diff", "--no-color"))[1])
    else:
        parts.append((await _git(root, "diff", "--cached", "--no-ext-diff", "--no-color"))[1])
    _, listed, _ = await _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    for path in [p for p in listed.split("\0") if p][:200]:
        if sum(map(len, parts)) > MAX_DIFF:
            break
        # Новый файл — как дифф с пустым: код выхода 1 означает «есть разница».
        parts.append((await _git(root, "diff", "--no-index", "--no-color", "--", os.devnull, path,
                                 check=False))[1])
    text, cut = _cut("".join(parts))
    return {"diff": text, "truncated": cut}


async def show(root: str, sha: str) -> dict:
    _require(root)
    if not _SHA.match(sha or ""):
        raise GitError("Некорректный идентификатор коммита")
    _, out, _ = await _git(root, "show", "--no-ext-diff", "--no-color",
                           f"--pretty=format:%H{_FIELD}%an{_FIELD}%aI{_FIELD}%B{_RECORD}", sha)
    meta, _, patch = out.partition(_RECORD)
    fields = meta.split(_FIELD)
    text, cut = _cut(patch.lstrip("\n"))
    return {"sha": fields[0] if fields else sha, "author": fields[1] if len(fields) > 1 else "",
            "date": fields[2] if len(fields) > 2 else "", "message": fields[3].strip() if len(fields) > 3 else "",
            "diff": text, "truncated": cut}


# --- изменения ---------------------------------------------------------------

async def init(root: str, branch: str = "main") -> dict:
    if is_repo(root):
        return await status(root)
    await _git(root, "init", "-q", "-b", branch)
    return await status(root)


async def commit(root: str, message: str, author: str, email: str) -> dict:
    """Закоммитить все изменения проекта (git add -A)."""
    message = (message or "").strip()
    if not message:
        raise GitError("Нужно сообщение коммита")
    if not is_repo(root):
        await init(root)
    await _git(root, "add", "-A")
    code, _, _ = await _git(root, "diff", "--cached", "--quiet", check=False)
    if code == 0:
        raise GitError("Нечего коммитить: изменений нет")
    identity = {"user.name": author or "Layla", "user.email": email or "layla@localhost"}
    await _git(root, "commit", "-q", "-m", message[:5000], config=identity)
    _, stat, _ = await _git(root, "show", "--stat", "--format=", "HEAD")
    head = (await log(root, 1))[0]
    return {**head, "stat": stat.strip()[-2000:]}


async def set_remote(root: str, url: str) -> dict:
    _require(root)
    url = validate_remote(url)
    code, _, _ = await _git(root, "remote", "get-url", "origin", check=False)
    await _git(root, "remote", "set-url" if code == 0 else "add", "origin", url)
    return await status(root)


async def _remote_url(root: str) -> str:
    code, out, _ = await _git(root, "remote", "get-url", "origin", check=False)
    if code != 0 or not out.strip():
        raise GitError("У проекта нет удалённого репозитория: укажите его адрес (Код → Git).")
    return out.strip()


async def push(root: str, token: str | None, username: str | None = None, branch: str | None = None) -> dict:
    _require(root)
    url = await _remote_url(root)
    state = await status(root)
    branch = branch or state.get("branch") or "main"
    if not _BRANCH.match(branch):
        raise GitError("Некорректное имя ветки")
    if not state.get("last_commit"):
        raise GitError("Сначала сделайте коммит")
    config = _auth(url, username, token) if token else {}
    _, out, err = await _git(root, "push", "--porcelain", "-u", "origin", f"HEAD:refs/heads/{branch}",
                             config=config, timeout=180, secret=token)
    return {"branch": branch, "output": (out + err).strip()[-3000:], "status": await status(root)}


async def pull(root: str, token: str | None, username: str | None = None) -> dict:
    _require(root)
    url = await _remote_url(root)
    config = _auth(url, username, token) if token else {}
    state = await status(root)
    if state.get("upstream"):
        args = ["pull", "--ff-only", "--no-rebase"]
    else:
        args = ["pull", "--ff-only", "--no-rebase", "origin", state.get("branch") or "main"]
    _, out, err = await _git(root, *args, config=config, timeout=180, secret=token)
    return {"output": (out + err).strip()[-3000:], "status": await status(root)}
