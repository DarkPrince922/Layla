"""Bounded project-relative glob sets shared by Code rules and skills."""
from __future__ import annotations

import re
import time
from functools import lru_cache

import regex
from wcmatch import glob
from wcmatch._wcparse import PatternLimitException

FLAGS = glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB | glob.NEGATE | glob.NEGATEALL | glob.DOTMATCH | glob.FORCEUNIX


def split_patterns(value):
    if value is None:
        return []
    if isinstance(value, str):
        result, start, stack, escaped = [], 0, [], False
        for index, char in enumerate(value):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char in "{[(":
                stack.append(char)
            elif char in "}])":
                if stack:
                    stack.pop()
            elif char in ",\n" and not stack:
                result.append(value[start:index].strip())
                start = index + 1
        value = [*result, value[start:].strip()]
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("paths: нужен список или строка, максимум 50 шаблонов")
    result = []
    for item in value:
        if not isinstance(item, str) or len(item) > 300 or "\0" in item:
            raise ValueError("paths: каждый шаблон — строка до 300 символов")
        item = item.strip()
        if not item:
            continue
        target = item[1:] if item.startswith("!") and not item.startswith("!(") else item
        if target.startswith(("/", "~")) or ".." in target.split("/") or re.match(r"^[A-Za-z]:", target):
            raise ValueError("paths: разрешены только пути внутри проекта")
        result.append(item.removeprefix("./"))
    compiled(tuple(result))  # validate expansion/complexity before accepting guidance
    return result


@lru_cache(maxsize=512)
def compiled(patterns):
    if not patterns:
        return (), ()
    try:
        includes, excludes = glob.translate(patterns, flags=FLAGS, limit=200)
        return tuple(regex.compile(p) for p in includes), tuple(regex.compile(p) for p in excludes)
    except (ValueError, OverflowError, regex.error, PatternLimitException) as exc:
        raise ValueError("Glob слишком сложный или превышает 200 вариантов") from exc


def matches(path, patterns, *, deadline=None):
    if not patterns:
        return True
    includes, excludes = compiled(tuple(patterns))
    deadline = deadline or time.monotonic() + 0.1
    def check(expression):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Превышен лимит времени сопоставления glob")
        try:
            return expression.fullmatch(path, timeout=min(0.005, remaining)) is not None
        except TimeoutError as exc:
            raise ValueError("Glob требует слишком много времени") from exc
    return any(check(p) for p in includes) and not any(check(p) for p in excludes)
