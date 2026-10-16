"""Markdown-aware instruction includes and explicit skill command segments."""
from __future__ import annotations

import re
from uuid import uuid4

from markdown_it import MarkdownIt

MARKDOWN = MarkdownIt("commonmark")


def includes(text):
    marker = uuid4().hex
    text = strip_comments(text).replace("\\ ", marker + "space").replace("\\@", marker + "at")
    for token in MARKDOWN.parse(text):
        fragments = []
        if token.type == "inline":
            fragments = [child.content for child in token.children or [] if child.type == "text"]
        elif token.type == "html_block" and token.content.lstrip().startswith("<!--"):
            fragments = [re.sub(r"<!--[\s\S]*?-->", "", token.content)]
        for fragment in fragments:
            for raw in re.findall(r"(?:^|\s)@((?:[^\s\\]|\\ )+)", fragment):
                path = raw.replace(marker + "space", " ").split("#", 1)[0]
                if marker + "at" in path:
                    continue
                if path and re.match(r"^[A-Za-z0-9._-]", path) and not path.startswith(("/", "~", "@")):
                    yield path


def strip_comments(text):
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    protected = [(offsets[token.map[0]], offsets[token.map[1]]) for token in MARKDOWN.parse(text)
                 if token.map and token.type in {"fence", "code_block"}]
    pieces, cursor, index = [], 0, 0
    while index < len(text):
        block = next((stop for start, stop in protected if start <= index < stop), None)
        if block is not None:
            index = block
        elif text[index] == "\\":
            index += 2
        elif text[index] == "`":
            delimiter = re.match(r"`+", text[index:])[0]
            end = re.search(r"(?<!`)" + re.escape(delimiter) + r"(?!`)", text[index + len(delimiter):])
            index += len(delimiter) + (end.end() if end else 0)
        elif text.startswith("<!--", index):
            pieces.append(text[cursor:index])
            end = text.find("-->", index + 4)
            index = end + 3 if end >= 0 else len(text)
            cursor = index
        else:
            index += 1
    return "".join(pieces) + text[cursor:]


def commands(text):
    """Source offsets only: inserted arguments/output are never scanned again."""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    protected, found = [], []
    for token in MARKDOWN.parse(text):
        if token.map is None:
            continue
        start, stop = offsets[token.map[0]], offsets[token.map[1]]
        if token.type in ("code_block", "html_block", "fence"):
            protected.append((start, stop))
            if token.type == "fence" and token.info.strip() == "!":
                found.append((start, stop, token.content.strip()))
        elif token.type == "inline":
            source = text[start:stop]
            # Explicit !`command` only. Ordinary code spans, escaped markers,
            # and HTML comments are not command sources.
            for match in re.finditer(r"(?<!\S)!`([^`\n]+)`", source):
                prefix = source[:match.start()]
                if prefix.count("<!--") > prefix.count("-->"):
                    continue
                before = re.sub(r"``[^`]*``|`[^`]*`", "", prefix)
                if before.count("`") % 2:
                    continue
                found.append((start + match.start(), start + match.end(), match[1].strip()))
    return sorted({(a, b, command) for a, b, command in found if command})
