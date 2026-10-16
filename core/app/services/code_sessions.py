"""Append-only worker protocol history; compaction never rewrites the journal."""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.models.chat import Message
from app.models.code_control import CodeContextView
from app.models.code_session import CodeLeadEntry, CodeLeadTurn
from app.models.code_team import CodeTranscript
from app.services import code_teams


class Conversation(list):
    def __init__(self, messages):
        super().__init__(deepcopy(messages))
        self.pending = []

    def append(self, message):
        super().append(message)
        self.pending.append(deepcopy(message))

    def extend(self, messages):
        for message in messages:
            self.append(message)


def repair(messages):
    """Complete interrupted protocol groups without replaying an uncertain effect."""
    pending = {}
    for message in messages:
        if message.get("role") == "assistant":
            pending.update({call["id"]: call for call in message.get("tool_calls", [])})
        elif message.get("role") == "tool":
            pending.pop(message.get("tool_call_id"), None)
    return [{"role": "tool", "tool_call_id": ident, "is_error": True,
             "content": '{"error":"Interrupted before the tool result was recorded. Outcome unknown; inspect files and state before retrying."}'}
            for ident in pending]


class Transcript:
    def __init__(self, manager, worker_id, team=None):
        self.manager, self.worker_id, self.team = manager, worker_id, team
        self.sequence = 0

    async def load(self, task):
        async with self.manager.maker() as session:
            await self.manager.owned(session, self.worker_id)
            rows = list(await session.scalars(select(CodeTranscript).where(
                CodeTranscript.worker_id == self.worker_id).order_by(CodeTranscript.sequence)))
            self.sequence = rows[-1].sequence + 1 if rows else 0
            messages = [row.message for row in rows]
        if not messages:
            messages = [{"role": "user", "content": task}]
            self.seed = messages
        else:
            self.seed = []
            # A provider must never receive dangling tool calls. Do not silently rerun
            # a tool whose side effect may have completed before the server crashed.
            self.seed = repair(messages)
            self.seed.append({"role": "user", "content": (
                "[Code runtime] Resume the original assignment in the existing worktree. "
                "Prior tool results are saved below; verify uncertain effects before retrying. "
                "Do not repeat completed work. New team mail, if any, follows.")})
            messages = [*messages, *self.seed]
        return deepcopy(messages)

    def conversation(self, messages):
        result = Conversation(messages)
        result.pending.extend(deepcopy(self.seed))
        return result

    async def flush(self, conversation, *, receive=False):
        async with self.manager.maker() as session:
            await self.manager.owned(session, self.worker_id)
            rows = await self.team.pending(session) if receive and self.team else []
            incoming = code_teams.envelope(rows) if rows else None
            pending_count = len(conversation.pending)
            batch = [*conversation.pending, *([incoming] if incoming else [])]
            for index, message in enumerate(batch):
                session.add(CodeTranscript(worker_id=self.worker_id, sequence=self.sequence + index, message=message))
            for row in rows:
                row.delivered = True
            # The mailbox ack and its full protocol message commit together.
            await session.commit()
            self.sequence += len(batch)
            del conversation.pending[:pending_count]
            if incoming:
                list.append(conversation, incoming)
        return bool(rows)


class LeadTranscript:
    """One writer per chat.run turn; each turn has an independently scoped journal.

    UI summaries and rollbacks select the replay view. The original rows remain
    available for inspection; neither operation rewrites the recorded protocol.
    """
    def __init__(self, runtime, turn_id, input_id, team=None, *, provider_id=None, model=None):
        self.runtime, self.turn_id, self.input_id, self.team = runtime, turn_id, input_id, team
        self.sequence = 0
        self.seed = []
        self.provider_id, self.model = provider_id, model

    async def authorize(self, session):
        await self.runtime._authorize(session)
        turn = await session.get(Message, self.turn_id)
        source = await session.get(Message, self.input_id)
        if (turn is None or source is None or turn.chat_id != self.runtime.chat_id or
                source.chat_id != self.runtime.chat_id or turn.role != "assistant" or source.role != "user"):
            raise ValueError("Сессия ведущего недоступна в этом Code-чате")

    async def load(self, payload):
        from app.services import history

        async with self.runtime.maker() as session:
            await self.authorize(session)
            current = await session.get(CodeLeadTurn, self.turn_id)
            if current is not None:
                raise ValueError("Этот ход ведущего уже запускался; отправьте новое сообщение для продолжения")
            visible = await history.messages(session, self.runtime.chat_id)
            summary = next((row for row in reversed(visible) if history.is_summary(row)), None)
            turns = list(await session.scalars(select(CodeLeadTurn).where(
                CodeLeadTurn.chat_id == self.runtime.chat_id, CodeLeadTurn.project_id == self.runtime.project_id,
                CodeLeadTurn.owner_id == self.runtime.owner_id, CodeLeadTurn.active.is_(True)
            ).order_by(CodeLeadTurn.created_at, CodeLeadTurn.id)))
            replay = []
            for turn in turns:
                # A summary may split a user/assistant pair: retain the entire
                # protocol group when its assistant falls after the boundary.
                if summary is not None and turn.created_at.replace(tzinfo=UTC) <= summary.created_at.replace(tzinfo=UTC):
                    continue
                entries = list(await session.scalars(select(CodeLeadEntry).where(
                    CodeLeadEntry.turn_id == turn.id).order_by(CodeLeadEntry.sequence)))
                view = await session.get(CodeContextView, turn.id)
                messages = ([*view.messages, *[entry.message for entry in entries if entry.sequence >= view.next_sequence]]
                            if view else [entry.message for entry in entries])
                if view:
                    replay = []  # This compact view already covers previous turns.
                if turn.provider_id != self.provider_id or turn.model != self.model:
                    messages = [{key: value for key, value in item.items() if key in
                        ("role", "content", "tool_calls", "tool_call_id", "is_error")} for item in messages]
                replay.extend(messages)
                replay.extend(repair(messages))
            input_row = await session.get(Message, self.input_id)
            source = {"role": "user", "content": input_row.content}
            if turns:
                self.seed = [source]
            else:
                # Adopt visible pre-journal history once, without persisting
                # persona/system prompts that must follow current settings.
                self.seed = [deepcopy(item) for item in payload if item.get("role") != "system"]
                if not self.seed or self.seed[-1] != source:
                    self.seed.append(source)
            session.add(CodeLeadTurn(id=self.turn_id, input_id=self.input_id,
                owner_id=self.runtime.owner_id, chat_id=self.runtime.chat_id,
                project_id=self.runtime.project_id, provider_id=self.provider_id, model=self.model,
                created_at=(await session.get(Message, self.turn_id)).created_at))
            await session.flush()
            for index, message in enumerate(self.seed):
                session.add(CodeLeadEntry(turn_id=self.turn_id, sequence=index, message=message))
            self.sequence = len(self.seed)
            await session.commit()
        # Persona instructions and the chosen compact summary are current,
        # while tools always come from the durable journal, never UI previews.
        systems = [deepcopy(item) for item in payload if item.get("role") == "system"]
        return [*systems, *deepcopy(replay), *deepcopy(self.seed)]

    def conversation(self, messages):
        return Conversation(messages)

    async def flush(self, conversation, *, receive=False):
        async with self.runtime.maker() as session:
            await self.authorize(session)
            turn = await session.get(CodeLeadTurn, self.turn_id)
            if turn is None or not turn.active:
                raise ValueError("Сессия уже отменена откатом")
            rows = await self.team.pending(session) if receive and self.team else []
            incoming = code_teams.envelope(rows) if rows else None
            count = len(conversation.pending)
            batch = [*conversation.pending, *([incoming] if incoming else [])]
            for index, message in enumerate(batch):
                session.add(CodeLeadEntry(turn_id=self.turn_id, sequence=self.sequence + index, message=message))
            for row in rows:
                row.delivered = True
            if getattr(self, "compacted", False):
                view = await session.get(CodeContextView, self.turn_id)
                if view is None:
                    view = CodeContextView(turn_id=self.turn_id)
                    session.add(view)
                view.next_sequence = self.sequence + len(batch)
                view.messages = [deepcopy(item) for item in conversation if item.get("role") != "system"]
            await session.commit()
            self.compacted = False
            self.sequence += len(batch)
            del conversation.pending[:count]
            if incoming:
                list.append(conversation, incoming)
        return bool(rows)

    async def finish(self, conversation, failed=False):
        if getattr(self, "transitioning", False) and not failed:
            return
        complete = (not failed and bool(conversation) and conversation[-1].get("role") == "assistant"
                    and not conversation[-1].get("tool_calls"))
        async with self.runtime.maker() as session:
            await self.authorize(session)
            await session.execute(update(CodeLeadTurn).where(CodeLeadTurn.id == self.turn_id).values(
                status="done" if complete else "interrupted", updated_at=datetime.now(UTC)))
            await session.commit()


async def invalidate(session, chat_id, message_ids):
    await session.execute(update(CodeLeadTurn).where(CodeLeadTurn.chat_id == chat_id,
        CodeLeadTurn.id.in_(message_ids)).values(active=False))


async def compact_items(session, rows):
    """Summaries include actual tool outputs, not just rendered chat reports."""
    from app.services import history

    turns = set(await session.scalars(select(CodeLeadTurn.id).where(
        CodeLeadTurn.id.in_([row.id for row in rows]), CodeLeadTurn.active.is_(True))))
    items = []
    for row in rows:
        if row.id in turns:
            entries = list(await session.scalars(select(CodeLeadEntry).where(
                CodeLeadEntry.turn_id == row.id).order_by(CodeLeadEntry.sequence)))
            for entry in entries:
                item = entry.message
                content = item.get("content") or ""
                if item.get("tool_calls"):
                    import json
                    content += "\nTool calls: " + json.dumps(item["tool_calls"], ensure_ascii=False)
                if content:
                    items.append({"role": item["role"], "content": content})
        elif (item := history.entry(row)) is not None:
            items.append(item)
    return items


@asynccontextmanager
async def recording(transcript, conversation):
    from app.services.code_context import RECORDER
    token = RECORDER.set(transcript)
    failed = True
    try:
        yield
        failed = False
    finally:
        try:
            if transcript is not None:
                await transcript.flush(conversation)
                if hasattr(transcript, "finish"):
                    await transcript.finish(conversation, failed)
        finally:
            RECORDER.reset(token)
