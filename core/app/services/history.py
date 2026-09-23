"""История чата для модели и её сжатие.

Длинный разговор не обрывается и не теряет начало: когда история перестаёт
помещаться в бюджет контекста, старая часть сворачивается моделью в сводку.
Сводка — системное сообщение чата (meta.kind = "summary"), вставленное сразу за
последним свёрнутым сообщением. Всё, что раньше неё, модели больше не
отправляется; всё, что позже, уходит как есть. Следующее сжатие дополняет
предыдущую сводку, так что чат может длиться сколько угодно.
"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat, Message
from app.models.persona import Persona
from app.services import project_agent

SUMMARY = "summary"
KEEP_RECENT = 6  # последние сообщения всегда уходят модели целиком
DEFAULT_BUDGET = 80_000  # токенов истории, если размер контекста модели не задан
SUMMARY_MAX_OUTPUT = 4096
_ITEM_CHARS = 12_000  # одно сообщение в сводке длиннее не нужно

SUMMARY_PROMPT = (
    "Ты сжимаешь историю рабочего разговора, чтобы продолжить работу без неё. Напиши сводку "
    "на языке разговора, по пунктам:\n"
    "1. Цель и задачи пользователя.\n"
    "2. Принятые решения и договорённости (стек, стиль, ограничения).\n"
    "3. Что уже сделано: файлы и изменения по путям.\n"
    "4. Текущее состояние и нерешённые проблемы.\n"
    "5. Следующие шаги.\n"
    "6. Важные детали дословно: имена, пути, значения, требования пользователя.\n"
    "Не выдумывай и не пересказывай лишнего. Только сводка, без вступления."
)
SUMMARY_HEADER = "Сводка более ранней части разговора (старые сообщения свёрнуты, чтобы уместиться в контекст):\n"


def is_summary(message: Message) -> bool:
    return message.role == "system" and (message.meta or {}).get("kind") == SUMMARY


def entry(message: Message) -> dict | None:
    """Сообщение чата в том виде, в каком его видит модель."""
    if message.role not in ("user", "assistant", "system") or is_summary(message):
        return None
    meta = message.meta or {}
    content = message.content or ""
    changes = [t["change"] for t in meta.get("tools", []) if t.get("change") and t.get("status") == "done"]
    if changes:
        listed = ", ".join(f"{c['operation']} {c['path']}" for c in changes)
        if meta.get("rolled_back"):
            content += f"\n[These project changes were later rolled back by the user: {listed}]"
        else:
            content += f"\n[Applied project changes: {listed}]"
    if message.role == "assistant" and meta.get("error"):
        # Ход прерван (ошибка, остановка, перезапуск сервера): модели нужно знать, что уже
        # сделано, чтобы «Продолжить» подхватило работу, а не начало её заново.
        steps = [f"{t.get('name')} {t.get('path') or ''} — {t.get('status')}".strip()
                 for t in meta.get("tools", [])[-30:]]
        done = ("; ".join(steps)) if steps else "ничего"
        content += (f"\n[This turn was interrupted: {meta['error']} Steps done before that: {done}. "
                    "Files already reflect the completed steps. When asked to continue, pick up from "
                    "here without repeating completed steps.]")
    todos = meta.get("todos") if message.role == "assistant" else None
    if todos:
        marks = {"done": "✓", "in_progress": "→", "pending": "☐"}
        listed = "; ".join(f"{marks.get(t.get('status'), '☐')} {t.get('content')}" for t in todos)
        content += f"\n[Task list at the end of this turn: {listed}]"
    if not content:
        return None
    # Размышления прошлых ответов модели не отправляются: это черновик мысли, часто с
    # кодом целиком, — он раздувал бы каждый следующий запрос. Внутри текущего хода
    # (между вызовами инструментов) их по-прежнему передаёт project_agent.
    return {"role": message.role, "content": content}


async def messages(session: AsyncSession, chat_id: str) -> list[Message]:
    return list(await session.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)))


def _after_summary(rows: list[Message]) -> tuple[Message | None, list[Message]]:
    """Последняя сводка и сообщения после неё."""
    summary, tail = None, []
    for row in rows:
        if is_summary(row):
            summary, tail = row, []
        else:
            tail.append(row)
    return summary, tail


async def build(session: AsyncSession, chat: Chat) -> list[dict]:
    """Payload для модели: инструкции роли, сводка (если была), история после неё."""
    out: list[dict] = []
    if chat.persona_id:
        persona = await session.get(Persona, chat.persona_id)
        if persona and persona.instructions:
            out.append({"role": "system", "content": persona.instructions})
    summary, tail = _after_summary(await messages(session, chat.id))
    if summary is not None:
        out.append({"role": "system", "content": SUMMARY_HEADER + summary.content})
    out.extend(item for item in map(entry, tail) if item)
    return out


def budget(caps: dict) -> int:
    context = caps.get("context")
    return max(2048, int(context * 0.6)) if context else DEFAULT_BUDGET


def tokens(payload: list[dict]) -> int:
    return sum(project_agent._tokens(m) for m in payload)


def needed(payload: list[dict], caps: dict) -> bool:
    return tokens(payload) > budget(caps)


def _transcript(items: list[dict]) -> list[str]:
    names = {"user": "Пользователь", "assistant": "Ассистент", "system": "Система"}
    out = []
    for item in items:
        text = item["content"]
        if len(text) > _ITEM_CHARS:
            text = text[: _ITEM_CHARS // 2] + "\n…[середина опущена]…\n" + text[-_ITEM_CHARS // 2:]
        out.append(f"### {names.get(item['role'], item['role'])}\n{text}")
    return out


async def summarize(provider, key, model: str, caps: dict, previous: str | None, items: list[dict],
                    on_event=None) -> str:
    """Свернуть сообщения в сводку. Очень длинную историю — по частям, дополняя сводку."""
    turn_caps = {k: v for k, v in caps.items() if k not in ("temperature",)}
    limit = caps.get("max_output_cap") or caps.get("max_output") or SUMMARY_MAX_OUTPUT
    turn_caps["max_output"] = min(SUMMARY_MAX_OUTPUT, limit)
    chunk_budget = max(2000, budget(caps) // 2)
    parts, chunk, size = [], [], 0
    for text in _transcript(items):
        cost = len(text) // 3 + 4
        if chunk and size + cost > chunk_budget:
            parts.append(chunk)
            chunk, size = [], 0
        chunk.append(text)
        size += cost
    if chunk:
        parts.append(chunk)
    summary = previous or ""
    for chunk in parts:
        request = ("Предыдущая сводка:\n" + summary + "\n\n" if summary else "") + \
            "Сообщения, которые нужно добавить в сводку:\n\n" + "\n\n".join(chunk)
        conversation = [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": request}]
        text = ""
        async for kind, value in project_agent._turn(provider, key, model, conversation, [], turn_caps):
            if kind == "done":
                text = "".join(value[0])
            elif on_event is not None:
                await on_event(kind, value)
        text = text.replace(project_agent.LENGTH_NOTE, "").strip()
        if text:
            summary = text
    return summary.strip()


async def compact(session: AsyncSession, chat: Chat, provider, key, model: str, caps: dict, *,
                  keep: int = KEEP_RECENT, on_event=None) -> Message | None:
    """Свернуть всё, кроме последних keep сообщений, в сводку. None — сворачивать нечего."""
    rows = await messages(session, chat.id)
    previous, tail = _after_summary(rows)
    candidates = [row for row in tail if entry(row)]
    older = candidates[:-keep] if keep else candidates
    if not older:
        return None
    text = await summarize(provider, key, model, caps, previous.content if previous else None,
                           [entry(row) for row in older], on_event=on_event)
    if not text:
        return None
    count = len(older) + int(((previous.meta or {}).get("count") or 0) if previous else 0)
    last = older[-1]
    # Сразу за последним свёрнутым сообщением: всё до сводки модели больше не отправляется.
    summary = Message(chat_id=chat.id, role="system", content=text,
                      meta={"kind": SUMMARY, "count": count, "until": last.id},
                      created_at=last.created_at + timedelta(microseconds=1))
    session.add(summary)
    await session.commit()
    return summary


# --- Ужатие внутри длинного хода агента ---------------------------------------

RUN_BUDGET = 100_000  # токенов на ход, если контекст модели не задан
KEEP_ROUNDS = 2  # последние шаги агента не трогаем
_HIDDEN = "содержимое скрыто для экономии контекста — прочитайте файл заново, если нужно"
_BIG_ARGS = ("content", "new_string", "old_string")


def _shrink_tool(message: dict) -> dict | None:
    try:
        data = json.loads(message.get("content") or "")
    except ValueError:
        data = None
    if isinstance(data, dict) and "content" in data and "sha256" in data:
        slim = {"path": data.get("path"), "sha256": data["sha256"], "note": _HIDDEN}
    elif isinstance(data, dict) and isinstance(data.get("files"), list) and len(data["files"]) > 40:
        slim = {"files": data["files"][:40], "note": f"ещё {len(data['files']) - 40} — список сокращён"}
    elif len(message.get("content") or "") > 2000:
        return {**message, "content": message["content"][:1500] + "…[сокращено]"}
    else:
        return None
    return {**message, "content": json.dumps(slim, ensure_ascii=False)}


def _shrink_call(message: dict) -> dict | None:
    changed, calls = False, []
    for call in message.get("tool_calls") or []:
        try:
            args = json.loads(call["function"]["arguments"])
        except (ValueError, KeyError, TypeError):
            calls.append(call)
            continue
        for field in _BIG_ARGS:
            value = args.get(field) if isinstance(args, dict) else None
            if isinstance(value, str) and len(value) > 300:
                args[field] = f"[{len(value)} символов — уже применено]"
                changed = True
        calls.append({**call, "function": {**call["function"], "arguments": json.dumps(args, ensure_ascii=False)}})
    return {**message, "tool_calls": calls} if changed else None


def shrink_run(conversation: list[dict], anchor: dict, caps: dict) -> int:
    """Ужать старые результаты инструментов и аргументы записей в текущем ходе."""
    context = caps.get("context")
    limit = (context - (caps.get("max_output") or 8192) - 256) if context else RUN_BUDGET
    limit = max(limit, 2048)
    total = tokens(conversation)
    if total <= limit:
        return 0
    start = next(i for i, m in enumerate(conversation) if m is anchor) + 1
    rounds = [i for i in range(start, len(conversation))
              if conversation[i]["role"] == "assistant" and conversation[i].get("tool_calls")]
    stop = rounds[-KEEP_ROUNDS] if len(rounds) >= KEEP_ROUNDS else start
    shrunk = 0
    for index in range(start, stop):
        if total <= limit:
            break
        message = conversation[index]
        slim = _shrink_tool(message) if message["role"] == "tool" else _shrink_call(message)
        if slim is not None:
            total += project_agent._tokens(slim) - project_agent._tokens(message)
            conversation[index] = slim
            shrunk += 1
    return shrunk
