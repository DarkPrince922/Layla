"""Explicit transcript import/export and isolated forks of owned Code chats."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.chat import Chat, Message
from app.models.code_session import CodeLeadEntry, CodeLeadTurn, CodeSessionArchive
from app.models.enums import Domain
from app.models.job import Job
from app.services import jobs


class Import(BaseModel):
    text: str = Field(min_length=1, max_length=10_000_000)
    leaf_uuid: str | None = Field(default=None, max_length=200)
    title: str = Field(default="Импортированная сессия", max_length=300)


class Fork(BaseModel):
    title: str = Field(default="Копия сессии", max_length=300)


def normalize(text, leaf=None):
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(value, dict):
        value = value.get("messages", value.get("entries"))
    if not isinstance(value, list) or not value or len(value) > 10000 or any(not isinstance(row, dict) for row in value):
        raise ValueError("Нужен JSON/JSONL журнал, не более 10000 записей")
    raw = value
    indexed = {row["uuid"]: row for row in value if isinstance(row.get("uuid"), str)}
    if len(indexed) != sum(isinstance(row.get("uuid"), str) for row in value):
        raise ValueError("Повторяющиеся UUID журнала")
    if indexed:
        if leaf:
            tip = indexed.get(leaf)
            if tip is None:
                raise ValueError("Ветка журнала не найдена")
        else:
            parents = {row.get("parentUuid") for row in indexed.values()}
            tips = [row for row in indexed.values() if row["uuid"] not in parents and not row.get("isSidechain") and row.get("type") in ("user", "assistant")]
            if not tips:
                raise ValueError("В журнале нет основной ветки")
            tip = tips[-1]
        value, seen = [], set()
        while tip:
            if tip["uuid"] in seen:
                raise ValueError("Цикл parentUuid в журнале")
            seen.add(tip["uuid"]); value.append(tip)
            parent = tip.get("parentUuid")
            if parent and parent not in indexed:
                raise ValueError("В журнале отсутствует родитель; загрузите полную цепочку")
            tip = indexed.get(parent)
        value.reverse()
    messages, pending, used = [], {}, set()
    def close_pending():
        if pending:
            messages.extend({"role": "tool", "tool_call_id": key, "is_error": True,
                "content": "Imported transcript has no result. Outcome unknown; inspect current files before retrying."} for key in pending)
            pending.clear()
    for row in value:
        item = row.get("message", row)
        role = item.get("role", row.get("type")) if isinstance(item, dict) else None
        if role not in ("user", "assistant", "tool"):
            continue
        content = item.get("content", "")
        if isinstance(content, str):
            if role == "tool":
                ident = item.get("tool_call_id")
                if ident not in pending:
                    raise ValueError("Результат без вызова инструмента")
                pending.pop(ident)
                messages.append({"role": "tool", "tool_call_id": ident, "content": content})
            else:
                close_pending()
                result = {"role": role, "content": content}
                if role == "assistant" and item.get("tool_calls"):
                    result["tool_calls"] = deepcopy(item["tool_calls"])
                    for call in result["tool_calls"]:
                        ident = call.get("id")
                        if not isinstance(ident, str) or ident in used or not isinstance(call.get("function", {}).get("arguments"), str):
                            raise ValueError("Некорректный вызов инструмента")
                        used.add(ident); pending[ident] = True
                messages.append(result)
        elif isinstance(content, list):
            texts, calls = [], []
            for block in content:
                if not isinstance(block, dict):
                    raise TypeError("Некорректный блок сообщения")
                kind = block.get("type")
                if kind == "text":
                    texts.append(str(block.get("text", "")))
                elif kind == "tool_use" and role == "assistant":
                    ident = block.get("id")
                    if not isinstance(ident, str) or ident in used:
                        raise ValueError("Повторяющийся ID вызова")
                    used.add(ident)
                    calls.append({"id": ident, "type": "function", "function": {"name": block["name"], "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)}})
                elif kind == "tool_result" and role == "user":
                    ident = block.get("tool_use_id")
                    if ident not in pending:
                        raise ValueError("Результат без вызова инструмента")
                    pending.pop(ident)
                    result = block.get("content", "")
                    messages.append({"role": "tool", "tool_call_id": ident, "content": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False), "is_error": bool(block.get("is_error"))})
                elif kind in ("thinking", "redacted_thinking"):
                    continue  # Raw journal retains provider-bound signatures; never replay them to another provider.
                else:
                    raise ValueError("Этот тип вложения журнала пока не поддерживается: " + str(kind))
            if texts or calls:
                close_pending()
                result = {"role": role, "content": "\n".join(texts)}
                if calls:
                    result["tool_calls"] = calls
                    pending.update({call["id"]: True for call in calls})
                messages.append(result)
        else:
            raise TypeError("Некорректное содержимое сообщения")
    close_pending()
    if not messages:
        raise ValueError("В журнале нет сообщений")
    return messages, raw


async def idle(session, chat):
    from app.services import code_lead
    if (chat.owner_id, chat.id) in code_lead.ACTIVE_CHATS:
        raise ValueError("Дождитесь завершения ведущего перед копированием сессии")
    rows = await session.scalars(select(Job).where(Job.chat_id == chat.id, Job.status.in_(jobs.ACTIVE)))
    if any(not jobs.is_orphaned(row) for row in rows):
        raise ValueError("Дождитесь завершения задачи перед копированием сессии")


def new_chat(source, title):
    if source.domain != Domain.code or not source.project_id:
        raise HTTPException(status_code=404, detail="Нужен Code-чат проекта")
    return Chat(id=str(uuid4()), owner_id=source.owner_id, domain=Domain.code, project_id=source.project_id,
                workspace_id=source.workspace_id, persona_id=source.persona_id, model=source.model,
                provider_id=source.provider_id, title=title)


async def import_session(session, source, args):
    messages, raw = normalize(args.text, args.leaf_uuid)
    chat = new_chat(source, args.title)
    session.add(chat); await session.flush()
    now = datetime.now(UTC)
    visible = []
    for index, item in enumerate(messages):
        if item["role"] in ("assistant", "user"):
            visible.append(Message(id=str(uuid4()), chat_id=chat.id, role=item["role"], content=item.get("content", ""),
                                   created_at=now + timedelta(microseconds=index), meta={"imported": True}))
    seed = Message(id=str(uuid4()), chat_id=chat.id, role="user", content="Импорт журнала сессии", created_at=now - timedelta(microseconds=1), meta={"imported": True})
    end = Message(id=str(uuid4()), chat_id=chat.id, role="assistant", content="Журнал импортирован. Файлы проекта не восстанавливались из журнала.",
                  created_at=now + timedelta(microseconds=len(messages) + 1), meta={"imported": True})
    session.add(CodeSessionArchive(chat_id=chat.id, entries=raw))
    session.add_all([seed, *visible, end]); await session.flush()
    session.add(CodeLeadTurn(id=end.id, input_id=seed.id, owner_id=chat.owner_id, chat_id=chat.id,
                             project_id=chat.project_id, status="done", active=True))
    await session.flush()
    session.add_all(CodeLeadEntry(turn_id=end.id, sequence=index, message=item) for index, item in enumerate(messages))
    await session.commit()
    return chat


async def fork_session(session, source, title):
    await idle(session, source)
    chat = new_chat(source, title)
    session.add(chat); await session.flush()
    ids = {}
    rows = list(await session.scalars(select(Message).where(Message.chat_id == source.id).order_by(Message.created_at, Message.id)))
    for row in rows:
        ident = ids[row.id] = str(uuid4())
        meta = deepcopy(row.meta or {})
        meta.pop("checkpoint", None)
        session.add(Message(id=ident, chat_id=chat.id, role=row.role, content=row.content, meta=meta,
                            created_at=row.created_at, parent_id=row.id))
    await session.flush()
    archive = await session.scalar(select(CodeSessionArchive).where(CodeSessionArchive.chat_id == source.id))
    if archive:
        session.add(CodeSessionArchive(chat_id=chat.id, entries=deepcopy(archive.entries)))
    turns = list(await session.scalars(select(CodeLeadTurn).where(CodeLeadTurn.chat_id == source.id)))
    for turn in turns:
        session.add(CodeLeadTurn(id=ids[turn.id], input_id=ids[turn.input_id], owner_id=chat.owner_id, chat_id=chat.id,
            project_id=chat.project_id, active=turn.active, status="interrupted" if turn.status == "running" else turn.status,
            provider_id=turn.provider_id, model=turn.model, created_at=turn.created_at))
    await session.flush()
    for turn in turns:
        entries = await session.scalars(select(CodeLeadEntry).where(CodeLeadEntry.turn_id == turn.id).order_by(CodeLeadEntry.sequence))
        session.add_all(CodeLeadEntry(turn_id=ids[turn.id], sequence=row.sequence, message=deepcopy(row.message)) for row in entries)
    await session.commit()
    return chat
