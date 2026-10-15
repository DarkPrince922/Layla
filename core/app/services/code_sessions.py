"""Append-only worker protocol history; compaction never rewrites the journal."""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy

from sqlalchemy import select

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
            pending = {}
            for message in messages:
                if message.get("role") == "assistant":
                    pending.update({call["id"]: call for call in message.get("tool_calls", [])})
                elif message.get("role") == "tool":
                    pending.pop(message.get("tool_call_id"), None)
            self.seed = [{"role": "tool", "tool_call_id": ident, "is_error": True,
                          "content": '{"error":"Interrupted before the tool result was recorded. Outcome unknown; inspect files and state before retrying."}'}
                         for ident in pending]
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


@asynccontextmanager
async def recording(transcript, conversation):
    try:
        yield
    finally:
        if transcript is not None:
            await transcript.flush(conversation)
