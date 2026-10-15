"""Project skill snapshots, bounded YAML metadata and literal argument expansion."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import PurePosixPath

import yaml

from app.services import files

DIRECTORIES = (".layla/skills", ".claude/skills", ".agents/skills", ".layla/commands", ".claude/commands")
NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}"
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
          "user-invocable", "context", "agent", "model", "hooks", "version", "license", "metadata", "compatibility"}


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
    names = shlex.split(value.replace(",", " ")) if isinstance(value, str) else value
    if not isinstance(names, list) or len(names) > 100:
        raise ValueError("allowed-tools: нужен список или строка имён инструментов")
    result = set()
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:.-]{0,99}", name):
            raise ValueError("allowed-tools: ограничения аргументов вроде Bash(...) пока не поддерживаются")
        result.update(ALIASES.get(name, {name}))
    return result


class Skill:
    def __init__(self, name: str, path: str, document: dict):
        self.name, self.path, self.sha256, self.raw = name, path, document["sha256"], document["content"]
        self.meta, self.body = frontmatter(self.raw)
        if self.meta.get("name", name) != name:
            raise ValueError("Имя frontmatter должно совпадать с именем файла/папки skill")
        for flag in ("disable-model-invocation", "user-invocable"):
            if flag in self.meta and not isinstance(self.meta[flag], bool):
                raise ValueError(f"{flag}: нужно true или false")
        for field in ("description", "argument-hint", "context", "model", "agent"):
            if field in self.meta and (not isinstance(self.meta[field], str) or len(self.meta[field]) > 2000):
                raise ValueError(f"{field}: нужна строка до 2000 символов")
        self.allowed = tool_names(self.meta.get("allowed-tools"))

    def public(self):
        return {"name": self.name, "path": self.path, "sha256": self.sha256,
                "description": self.meta.get("description", ""), "argument_hint": self.meta.get("argument-hint", ""),
                "model_invocable": not self.meta.get("disable-model-invocation", False),
                "user_invocable": self.meta.get("user-invocable", True),
                "context": self.meta.get("context", ""), "unsupported": sorted(set(self.meta) - FIELDS)}

    def render(self, args: str, session_id: str, *, user=False) -> str:
        if user and not self.meta.get("user-invocable", True):
            raise ValueError("Этот skill недоступен как пользовательская slash-команда")
        if not user and self.meta.get("disable-model-invocation", False):
            raise ValueError("Для этого skill отключён автоматический вызов моделью; используйте /" + self.name)
        unsupported = sorted(set(self.meta) - FIELDS)
        if unsupported or self.meta.get("model", "inherit") != "inherit":
            raise ValueError("Поля skill пока не поддерживаются: " + ", ".join(unsupported or ["model"]))
        if self.meta.get("context", "") not in ("", "fork"):
            raise ValueError("context: поддерживается только fork или обычный контекст")
        if re.search(r"!`", self.body):
            raise ValueError("Динамическая подстановка shell в skill пока не поддерживается")
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

        expanded, count = re.subn(pattern, replace, self.body)
        if not count and args:
            expanded += "\n\nARGUMENTS: " + args
        if len(expanded) > 80000:
            raise ValueError("Развёрнутый skill слишком большой")
        return expanded


class Catalog:
    def __init__(self, root: str):
        self.skills: dict[str, Skill] = {}
        self.errors: dict[str, str] = {}
        seen = set()
        for directory in DIRECTORIES:
            try:
                entries = files.list_dir(root, directory)
            except FileNotFoundError:
                continue
            for entry in sorted(entries, key=lambda item: (
                item["name"] if item["is_dir"] else item["name"].removesuffix(".md"), item["is_dir"], item["name"],
            )):
                if len(seen) >= MAX_SKILLS:
                    self.errors["catalog"] = "Каталог ограничен 200 skills"
                    break
                name = entry["name"] if entry["is_dir"] else entry["name"].removesuffix(".md")
                if not re.fullmatch(NAME, name) or not entry["is_dir"] and not entry["name"].endswith(".md") or name in seen:
                    continue
                path = entry["path"] + "/SKILL.md" if entry["is_dir"] else entry["path"]
                try:
                    document = files.read_file(root, path)
                except FileNotFoundError:
                    continue
                except (ValueError, OSError) as exc:
                    seen.add(name)
                    self.errors[name] = str(exc)[:500]
                    continue
                seen.add(name)
                try:
                    self.skills[name] = Skill(name, path, document)
                except ValueError as exc:
                    self.errors[name] = str(exc)[:500]

    def get(self, name):
        if name in self.errors:
            raise ValueError(self.errors[name])
        if name not in self.skills:
            raise ValueError("Skill не найден: " + name)
        return self.skills[name]

    def context(self):
        items = [skill.public() for skill in self.skills.values() if skill.public()["model_invocable"]]
        return "Project skill catalog (snapshot; use invoke_skill to activate): " + json.dumps(items, ensure_ascii=False)[:12000]
