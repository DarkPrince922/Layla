"""Project skill snapshots, bounded YAML metadata and literal argument expansion."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import PurePosixPath

import yaml

from app.services import code_bundled_skills, code_markdown, code_patterns, code_skill_policy, files

DIRECTORIES = (".layla/skills", ".claude/skills", ".agents/skills", ".layla/commands", ".claude/commands")
SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}"
NAME = SEGMENT + r"(?::" + SEGMENT + r"){0,7}"
MAX_SKILLS = 200
ALIASES = {"Read": {"read_file"}, "Write": {"write_file"}, "Edit": {"edit_file", "append_file"},
           "Glob": {"glob_files"}, "Grep": {"grep_files"}, "Bash": {"run_command"},
           "Skill": {"invoke_skill"}, "Agent": {"agent_start"}, "LSP": {"lsp"},
           "Task": {"agent_start"}, "LS": {"list_files"}, "TodoWrite": {"update_todos"},
           "NotebookEdit": {"notebook_edit"}, "TaskCreate": {"task_create"},
           "TaskGet": {"task_get"}, "TaskList": {"task_list"}, "TaskUpdate": {"task_update"},
           "TaskOutput": {"worker_output", "worker_wait"}, "TaskStop": {"worker_stop"},
           "WebFetch": {"web_fetch"}, "WebSearch": {"web_search"}}
FIELDS = {"name", "description", "argument-hint", "arguments", "allowed-tools", "disable-model-invocation",
          "user-invocable", "context", "agent", "model", "hooks", "version", "license", "metadata", "compatibility", "paths", "when_to_use", "effort", "shell"}


class Loader(yaml.SafeLoader):
    pass


def mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("Frontmatter: повторяющийся или нестроковый ключ")
        result[key] = loader.construct_object(value_node)
    return result


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def frontmatter(text: str) -> tuple[dict, str]:
    if len(text.encode()) > 65536:
        raise ValueError("Skill превышает 64 КиБ")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("Frontmatter не закрыт разделителем ---")
    source = "".join(lines[1:end])
    if len(source) > 16000:
        raise ValueError("Frontmatter превышает 16000 символов")
    try:
        for index, token in enumerate(yaml.scan(source)):
            if index > 2000 or isinstance(token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)):
                raise ValueError("Frontmatter: aliases, anchors, tags или слишком сложный YAML недоступны")
        meta = yaml.load(source, Loader=Loader) or {}
    except yaml.YAMLError as exc:
        raise ValueError("Некорректный YAML frontmatter") from exc
    if not isinstance(meta, dict):
        raise ValueError("Frontmatter должен быть объектом")  # noqa: TRY004 — public validation contract
    return meta, "".join(lines[end + 1:])


def tool_names(value) -> set[str] | None:
    if value is None:
        return None
    result = set()
    for rule in code_skill_policy.parse(value):
        name, _argument = code_skill_policy.parts(rule)
        result.update(ALIASES.get(name, {name}))
    return result


class Skill:
    def __init__(self, name: str, path: str, document: dict, *, base="", source="project", bundled_files=None):
        self.name, self.path, self.sha256, self.raw = name, path, document["sha256"], document["content"]
        self.meta, self.body = frontmatter(self.raw)
        self.base, self.source = base, source
        self.bundled_files = bundled_files
        self.paths = code_patterns.split_patterns(self.meta.get("paths"))
        if "name" in self.meta and (not isinstance(self.meta["name"], str) or len(self.meta["name"]) > 200):
            raise ValueError("name: нужна строка до 200 символов")
        for flag in ("disable-model-invocation", "user-invocable"):
            if flag in self.meta and not isinstance(self.meta[flag], bool):
                raise ValueError(f"{flag}: нужно true или false")
        for field in ("description", "argument-hint", "context", "model", "agent", "when_to_use"):
            if field in self.meta and (not isinstance(self.meta[field], str) or len(self.meta[field]) > 2000):
                raise ValueError(f"{field}: нужна строка до 2000 символов")
        if self.meta.get("effort") not in (None, "low", "medium", "high", "max"):
            raise ValueError("effort: low, medium, high или max")
        if self.meta.get("shell") not in (None, "bash", "powershell"):
            raise ValueError("shell: bash или powershell")
        self.active = not self.paths
        self.allowed = tool_names(self.meta.get("allowed-tools"))
        self.rules = code_skill_policy.parse(self.meta.get("allowed-tools")) if self.allowed is not None else None

    def public(self):
        return {"name": self.name, "path": self.path, "sha256": self.sha256,
                "display_name": self.meta.get("name", self.name), "base": self.base or ".", "source": self.source,
                "paths": self.paths, "active": self.active,
                "description": self.meta.get("description") or self.body.strip().split("\n\n")[0][:500], "argument_hint": self.meta.get("argument-hint", ""),
                "model_invocable": not self.meta.get("disable-model-invocation", False),
                "user_invocable": self.meta.get("user-invocable", True),
                "context": self.meta.get("context", ""), "model": self.meta.get("model", "inherit"),
                "effort": self.meta.get("effort"), "when_to_use": self.meta.get("when_to_use", ""), "unsupported": sorted(set(self.meta) - FIELDS)}

    def render(self, args: str, session_id: str, *, user=False, dynamic=None) -> str:
        if user and not self.meta.get("user-invocable", True):
            raise ValueError("Этот skill недоступен как пользовательская slash-команда")
        if not user and self.meta.get("disable-model-invocation", False):
            raise ValueError("Для этого skill отключён автоматический вызов моделью; используйте /" + self.name)
        unsupported = sorted(set(self.meta) - FIELDS)
        if unsupported:
            raise ValueError("Поля skill пока не поддерживаются: " + ", ".join(unsupported))
        if self.meta.get("context", "") not in ("", "fork"):
            raise ValueError("context: поддерживается только fork или обычный контекст")
        segments = code_markdown.commands(self.body)
        if segments and dynamic is None:
            raise ValueError("Динамические команды skill выполняются только через invoke_skill и песочницу")
        names = self.meta.get("arguments", [])
        if isinstance(names, str):
            names = names.split()
        if not isinstance(names, list) or len(names) > 50 or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names
        ):
            raise ValueError("arguments: нужны имена аргументов")
        try:
            tokens = shlex.split(args)
        except ValueError as exc:
            raise ValueError("Аргументы skill: незакрытая кавычка") from exc
        named = dict(zip(names, tokens, strict=False))
        pattern = r"\$ARGUMENTS(?:\[(\d+)\])?|\$(\d+)(?!\w)|\$\{((?:CLAUDE|LAYLA)_(?:SKILL_DIR|SESSION_ID))\}"
        if names:
            pattern += r"|\$(" + "|".join(re.escape(name) for name in names) + r")(?![\w\[])"

        def replace(match):
            if match.group(1) is not None or match.group(2) is not None:
                index = int(match.group(1) or match.group(2))
                return tokens[index] if index < len(tokens) else ""
            if match.group(3):
                return str(PurePosixPath(self.path).parent) if match.group(3).endswith("SKILL_DIR") else session_id
            if names and match.group(4):
                return named.get(match.group(4), "")
            return args

        expanded, count, cursor = "", 0, 0
        if dynamic is not None and len(dynamic) != len(segments):
            raise ValueError("Неполный результат динамических команд skill")
        for index, (start, stop, _command) in enumerate(segments if dynamic is not None else []):
            fragment, substitutions = re.subn(pattern, replace, self.body[cursor:start])
            expanded += fragment + dynamic[index]
            count += substitutions
            cursor = stop
        fragment, substitutions = re.subn(pattern, replace, self.body[cursor:])
        expanded += fragment
        count += substitutions
        if not count and args:
            expanded += "\n\nARGUMENTS: " + args
        if len(expanded) > 80000:
            raise ValueError("Развёрнутый skill слишком большой")
        return expanded

    def resources(self, root, file=None):
        if self.bundled_files is not None:
            prefix = self.path.removesuffix("SKILL.md")
            if file is None:
                return {"files": [prefix + name for name in self.bundled_files], "truncated": False}
            name = "/".join(files._parts(file))
            if name not in self.bundled_files:
                raise ValueError("Встроенный ресурс skill не найден: " + name)
            return {"path": prefix + name, **code_bundled_skills.document(self.bundled_files[name])}
        directory = str(PurePosixPath(self.path).parent)
        if file is not None:
            parts = files._parts(file)
            target = str(PurePosixPath(directory) / "/".join(parts))
            document = {"content": self.raw, "sha256": self.sha256} if target == self.path else files.read_file(root, target)
            if len(document["content"]) > 60000:
                document = {**document, "content": document["content"][:60000], "truncated": True}
            return {"path": target, **document}
        result, pending = [], [(directory, 0)]
        while pending and len(result) < 100:
            current, depth = pending.pop()
            for entry in files.list_dir(root, current):
                if entry["is_dir"] and depth < 3:
                    pending.append((entry["path"], depth + 1))
                elif not entry["is_dir"]:
                    result.append(entry["path"])
                if len(result) >= 100:
                    break
        return {"files": result, "truncated": bool(pending) or len(result) >= 100}

    def dynamic(self, args, session_id):
        names = self.meta.get("arguments", [])
        names = names.split() if isinstance(names, str) else names
        tokens = shlex.split(args)
        values = {"LAYLA_SKILL_ARGUMENTS": args, "LAYLA_SKILL_DIR": str(PurePosixPath(self.path).parent),
                  "LAYLA_SKILL_SESSION_ID": session_id}
        for index, token in enumerate(tokens):
            values[f"LAYLA_SKILL_ARG_{index}"] = token
        named = {name: index for index, name in enumerate(names)}
        pattern = r"\$ARGUMENTS(?:\[(\d+)\])?|\$(\d+)(?!\w)|\$\{((?:CLAUDE|LAYLA)_(?:SKILL_DIR|SESSION_ID))\}"
        if names:
            pattern += r"|\$(" + "|".join(re.escape(n) for n in names) + r")(?![\w\[])"
        shell = self.meta.get("shell", "bash")
        def replace(match):
            if match[1] is not None or match[2] is not None:
                key = "LAYLA_SKILL_ARG_" + (match[1] or match[2])
            elif match[3]:
                key = "LAYLA_SKILL_DIR" if match[3].endswith("SKILL_DIR") else "LAYLA_SKILL_SESSION_ID"
            elif names and match[4]:
                key = "LAYLA_SKILL_ARG_" + str(named[match[4]])
            else:
                key = "LAYLA_SKILL_ARGUMENTS"
            values.setdefault(key, "")
            return "$env:" + key if shell == "powershell" else "${" + key + "}"
        commands = []
        for _start, _stop, command in code_markdown.commands(self.body):
            transformed = re.sub(pattern, replace, command)
            if shell == "powershell":
                prefix = "; ".join("$env:" + key + "='" + value.replace("'", "''") + "'" for key, value in values.items()) + "; "
            else:
                prefix = "export " + " ".join(key + "=" + shlex.quote(value) for key, value in values.items()) + "; "
            commands.append({"source": command, "command": prefix + transformed, "policy_command": transformed, "shell": shell})
        if len(commands) > 8 or sum(len(item["command"]) for item in commands) > 24000:
            raise ValueError("Skill ограничен 8 динамическими командами / 24000 символов")
        return commands


class Catalog:
    """Snapshot each ancestor on first access; never reread through read_skill."""
    def __init__(self, root: str, path=".", *, bundled=False):
        self.root, self.path = root, path
        self.skills: dict[str, Skill] = {}
        self.errors: dict[str, str] = {}
        self.variants, self.scanned, self.observed = {}, set(), set()
        self.bytes, self.visits = 0, 0
        self.bundled = {}
        if bundled:
            for name, resources in code_bundled_skills.documents():
                documents = dict(resources)
                self.bundled[name] = Skill(name, "bundled://layla/" + name + "/SKILL.md",
                    code_bundled_skills.document(documents["SKILL.md"]), source="bundled", bundled_files=documents)
        self.discover(path)

    def _base(self, base):
        if base in self.scanned:
            return
        self.scanned.add(base)
        from pathlib import PurePosixPath
        for rank, directory in enumerate(DIRECTORIES):
            directory = str(PurePosixPath(base) / directory) if base else directory
            pending = [(directory, "", 0)]
            while pending:
                current, namespace, depth = pending.pop()
                self.visits += 1
                if self.visits > 2000:
                    self.errors["catalog"] = "Превышен лимит 2000 каталогов skills"
                    return
                try:
                    entries = files.list_dir(self.root, current)
                except FileNotFoundError:
                    continue
                except (OSError, ValueError) as exc:
                    self.errors[current] = str(exc)[:500]
                    continue
                # Legacy command directories with SKILL.md use the directory name.
                skill_entry = next((e for e in entries if not e["is_dir"] and e["name"].lower() == "skill.md"), None)
                if skill_entry and namespace:
                    self._add(namespace, skill_entry["path"], base, rank)
                    continue
                for entry in sorted(entries, key=lambda e: (e["name"].removesuffix(".md"), e["is_dir"], e["name"])):
                    leaf = entry["name"] if entry["is_dir"] else entry["name"].removesuffix(".md")
                    if not re.fullmatch(SEGMENT, leaf):
                        continue
                    name = namespace + ":" + leaf if namespace else leaf
                    if entry["is_dir"] and depth < 8:
                        # Process in stable order; file.md has precedence over file/SKILL.md.
                        pending.append((entry["path"], name, depth + 1))
                    elif entry["name"].endswith(".md"):
                        self._add(name, entry["path"], base, rank)

    def _add(self, name, path, base, rank):
        if len(self.variants) >= MAX_SKILLS or self.bytes >= 1_000_000:
            self.errors["catalog"] = "Каталог ограничен 200 skills / 1 МБ"
            return
        key = base, name
        if key in self.variants:
            return
        try:
            document = files.read_file(self.root, path)
            self.bytes += len(document["content"].encode())
            value = Skill(name, path, document, base=base)
        except (OSError, ValueError) as exc:
            value = str(exc)[:500]
        self.variants[key] = (rank, value)

    def discover(self, path="."):
        target = files.safe_join(self.root, path)
        parts = files._parts(path)
        self.path = "/".join(parts) or "."
        self.observed.add(self.path)
        if len(self.observed) > 1000:
            raise ValueError("Превышен лимит путей обнаружения skills")
        self._base("")
        end = len(parts) + 1 if target.is_dir() else len(parts)
        for index in range(1, end):
            base = "/".join(parts[:index])
            if any(part in {".git", "node_modules", ".venv", "venv", "dist", "build", ".next", "__pycache__"} for part in parts[:index]):
                break
            self._base(base)
        chosen = {}
        self.skills = {}
        self.errors = {key: error for key, error in self.errors.items() if key == "catalog" or "/" in key}
        for (base, name), (rank, value) in self.variants.items():
            if base and self.path != base and not self.path.startswith(base + "/"):
                continue
            order = (len(PurePosixPath(base).parts), -rank)
            if name not in chosen or order > chosen[name][0]:
                chosen[name] = order, value
        for name, (_order, value) in chosen.items():
            if isinstance(value, str):
                self.errors[name] = value
            else:
                value.active = not value.paths or any(code_patterns.matches(
                    p[len(value.base) + 1:] if value.base else p, value.paths)
                    for p in self.observed if not value.base or p.startswith(value.base + "/"))
                self.skills[name] = value
        for name, skill in self.bundled.items():
            if name not in chosen:
                self.skills[name] = skill
        return self.public()

    def public(self, *, model=False):
        return {"skills": [skill.public() for skill in self.skills.values()
                           if not model or skill.active and skill.public()["model_invocable"]], "errors": self.errors}

    def get(self, name, *, user=False):
        if name in self.errors:
            raise ValueError(self.errors[name])
        if name not in self.skills:
            raise ValueError("Skill не найден: " + name)
        skill = self.skills[name]
        if not user and not skill.active:
            raise ValueError("Skill ещё не активирован подходящим путём из paths")
        return skill

    def context(self):
        # Keep complete JSON and report truncation, rather than cutting through metadata.
        result, size = [], 0
        for item in self.public(model=True)["skills"]:
            encoded = json.dumps(item, ensure_ascii=False)
            if size + len(encoded) > 12000:
                break
            result.append(item)
            size += len(encoded)
        return "Project skill catalog (snapshot; use invoke_skill to activate): " + json.dumps(
            {"skills": result, "truncated": len(result) < len(self.public(model=True)["skills"]),
             "errors": dict(list(self.errors.items())[:10])}, ensure_ascii=False)


def execution_model(provider, current, caps, requested="inherit", effort=None):
    """A skill can select an enabled model of the current owned provider only."""
    target = current if requested == "inherit" else requested
    if requested != "inherit":
        configured = {item["name"] for item in (getattr(provider, "models", None) or [])
                      if isinstance(item, dict) and item.get("name") and item.get("enabled", True)}
        default = getattr(provider, "default_model", None)
        disabled = {item.get("name") for item in (getattr(provider, "models", None) or [])
                    if isinstance(item, dict) and not item.get("enabled", True)}
        if default and default not in disabled:
            configured.add(default)
        if target not in configured:
            raise ValueError("Skill model должен быть включённой моделью текущего провайдера: " + str(target)[:200])
    chosen = dict(caps) if target == current else dict((getattr(provider, "model_caps", None) or {}).get(target) or {})
    if effort is not None:
        chosen["reasoning_effort"] = effort
    return target, chosen
