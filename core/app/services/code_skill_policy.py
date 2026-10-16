"""Narrow skill tool access, including constrained file/command arguments."""
from __future__ import annotations

import re
import shlex

from app.services import code_patterns


def merge(*groups):
    result, seen = [], set()
    for group in groups:
        for policy in group:
            key = tuple(policy)
            if key not in seen:
                result.append(policy)
                seen.add(key)
    if len(result) > 40:
        raise ValueError("Превышен лимит 40 наборов allowed-tools")
    return result


def parse(value):
    if value is None:
        return []
    if isinstance(value, str):
        items, start, depth, quoted, escaped = [], 0, 0, None, False
        for index, char in enumerate(value):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quoted:
                if char == quoted:
                    quoted = None
            elif char in "\"'":
                quoted = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif (char.isspace() or char == ",") and depth == 0:
                if value[start:index].strip():
                    items.append(value[start:index].strip())
                start = index + 1
        if depth or quoted:
            raise ValueError("allowed-tools: незакрытая группа/кавычка")
        value = [*items, *([value[start:].strip()] if value[start:].strip() else [])]
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("allowed-tools: максимум 100 правил")
    for rule in value:
        if not isinstance(rule, str) or len(rule) > 500:
            raise ValueError("allowed-tools: правило — строка до 500 символов")
        name, argument = parts(rule)
        if argument is not None:
            if name not in {"Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "Bash", "Agent", "Task", "WebFetch"}:
                raise ValueError("Аргументы allowed-tools поддерживаются для файлов, Bash, Agent/Task и WebFetch")
            if not argument.strip():
                raise ValueError("allowed-tools: пустое ограничение аргументов")
            if name in {"Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep"}:
                code_patterns.split_patterns(argument)
            if name == "Bash":
                command_tokens(argument.removesuffix(":*"))
            if name == "WebFetch":
                from app.services.code_web_http import url
                if not argument.startswith("domain:") or any(c in argument[7:] for c in "/?#"):
                    raise ValueError("WebFetch: ожидается domain:имя.домена")
                url("https://" + argument[7:])
    return value


def parts(rule):
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_:.-]{0,199})(?:\(([\s\S]*)\))?", rule)
    if not match:
        raise ValueError("Некорректное правило allowed-tools")
    return match[1], match[2]


def command_tokens(command):
    if not isinstance(command, str) or any(x in command for x in ("\n", "\r", "`", "$(", "${!")):
        raise ValueError("Ограниченный Bash не допускает compound/command substitution")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split, lexer.commenters = True, ""
    tokens = list(lexer)
    if not tokens or any(token and all(c in ";&|<>()" for c in token) for token in tokens):
        raise ValueError("Ограниченный Bash принимает только одну простую команду")
    return tokens


def allows(group, name, arguments):
    from app.services.code_skills import ALIASES
    for rule in group:
        tool, argument = parts(rule)
        if name not in ALIASES.get(tool, {tool}):
            continue
        if argument is None:
            return True
        if tool == "Bash":
            try:
                actual = command_tokens(arguments.get("command"))
                expected = command_tokens(argument.removesuffix(":*"))
            except ValueError:
                continue
            if (actual[:len(expected)] == expected if argument.endswith(":*") else actual == expected):
                return True
        elif tool in {"Agent", "Task"}:
            role = arguments.get("role", "implement")
            aliases = {"Explore": "explore", "Plan": "explore", "general-purpose": "implement"}
            if role == aliases.get(argument, argument):
                return True
        elif tool == "WebFetch":
            from urllib.parse import urlsplit

            from app.services.code_web_http import url
            try:
                if urlsplit(url(arguments.get("url", ""))).hostname == urlsplit(url("https://" + argument[7:])).hostname:
                    return True
            except ValueError:
                pass
        else:
            paths = arguments.get("paths") or [arguments.get("path", ".")]
            if isinstance(paths, list) and paths and all(isinstance(path, str) and code_patterns.matches(
                    path.removeprefix("./"), code_patterns.split_patterns(argument)) for path in paths):
                return True
    return False
