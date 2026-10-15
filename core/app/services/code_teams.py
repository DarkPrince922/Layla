"""Internal Code agents only: scoped teams and durable direct/broadcast messages."""
from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.code_team import CodeTeam, CodeTeamMessage, CodeTeamRequest
from app.models.code_worker import CodeWorker

NAME = r"^[a-zA-Z][a-zA-Z0-9_-]{0,59}$"


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Create(Empty):
    team_name: str = Field(pattern=NAME)
    description: str = Field(default="", max_length=2000)


class Send(Empty):
    chat_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    to: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=12000)
    summary: str = Field(default="", max_length=200)


class Request(Empty):
    kind: Literal["plan", "shutdown"]
    to: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=12000)


class Respond(Empty):
    request_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    approve: bool
    response: str = Field(default="", max_length=12000)


def request_public(row):
    return {"id": row.id, "kind": row.kind, "from": row.sender, "to": row.recipient,
            "content": row.content, "status": row.status, "response": row.response}


SCHEMAS = {"team_create": Create, "team_info": Empty, "team_delete": Empty, "send_message": Send, "team_request": Request, "team_respond": Respond}
DESCRIPTIONS = {
    "team_request": "Submit a durable plan to lead (worker only), or request graceful shutdown of a named worker (lead only). Returns request_id; use team_info to track the decision. A requested plan must be approved before a plan-first worker can resume implementation. Shutdown waits for the worker to accept at a tool boundary.",
    "team_respond": "Accept or reject a pending request addressed to you. Decisions are durable and cannot be changed. Approving shutdown makes the worker finish at the next tool boundary; files and output remain. Plan approval never grants permissions beyond the current persona/mode.",
    "team_create": "Create this Code chat's durable team, with you as lead. Only one active team per chat. agent_start(name=...) joins it; at most 3 active workers. Tasks/worktrees remain isolated. No external messaging.",
    "team_info": "Inspect this chat's team, named members, status and recent durable messages. Messages are automatically delivered between model turns. Failed/stopped agents keep their mailbox until resumed.",
    "team_delete": "Close the current team only after all member workers have stopped. Retain reports, mail and private worktrees; never discard edits or stop workers implicitly.",
    "send_message": "Send an internal message to a teammate by name, lead, or * for broadcast. Delivered at the receiver's next model turn, with a durable mailbox. Does not interrupt a running tool or automatically restart a finished worker. Use worker_resume to continue a stopped agent. Never grants additional tools or permissions. Lead may set chat_id to send to lead of another owned Code chat in the same project, with an active team; it does not automatically start that session.",
}
TOOLS = [{"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name],
          "parameters": schema.model_json_schema()}} for name, schema in SCHEMAS.items()]


def mail(row):
    return {"id": row.id, "sequence": row.sequence, "from": row.sender, "to": row.recipient, "summary": row.summary,
            "message": row.content, "delivered": row.delivered, "created_at": row.created_at.isoformat()}


class Runtime:
    def __init__(self, manager, worker_id=None):
        self.manager, self.worker_id = manager, worker_id

    def tools(self):
        if self.manager.permissions is not None and "files.read" not in self.manager.permissions:
            return []
        names = {"team_info"}
        if self.manager.mode not in ("plan", "review"):
            names |= {"send_message", "team_request", "team_respond"}
            if not self.worker_id:
                names |= {"team_create", "team_delete"}
        return [tool for tool in TOOLS if tool["function"]["name"] in names]

    async def identity(self, session):
        if self.manager.permissions is not None and "files.read" not in self.manager.permissions:
            raise ValueError("Персона не разрешает чтение проекта и команды")
        await self.manager.authorize(session)
        if self.worker_id:
            worker = await self.manager.owned(session, self.worker_id)
            team = await session.get(CodeTeam, worker.team_id) if worker.team_id else None
            actor = worker.member_name
        else:
            team = await session.scalar(select(CodeTeam).where(
                CodeTeam.chat_id == self.manager.chat_id, CodeTeam.active_slot == 1))
            actor = "lead"
        if team and (team.owner_id != self.manager.owner_id or team.project_id != self.manager.project_id or team.chat_id != self.manager.chat_id):
            raise ValueError("Команда не найдена в этом Code-чате")
        return team, actor

    async def info(self, team_id=None, *, before=None):
        async with self.manager.maker() as session:
            team, actor = await self.identity(session)
            if team_id:
                selected = await session.get(CodeTeam, team_id)
                if selected is None or selected.owner_id != self.manager.owner_id or selected.chat_id != self.manager.chat_id or selected.project_id != self.manager.project_id:
                    raise ValueError("Команда не найдена в этом Code-чате")
                if self.worker_id and (not team or team.id != selected.id):
                    raise ValueError("Воркер не состоит в этой команде")
                team = selected
            teams = list(await session.scalars(select(CodeTeam).where(CodeTeam.chat_id == self.manager.chat_id)
                                               .order_by(CodeTeam.created_at.desc(), CodeTeam.id.desc()).limit(50)))
            history = [{"id": row.id, "name": row.name, "active": row.active_slot == 1} for row in teams]
            if team is None:
                return {"team": None, "messages": [], "teams": history}
            members = list(await session.scalars(select(CodeWorker).where(CodeWorker.team_id == team.id)
                                                 .order_by(CodeWorker.created_at, CodeWorker.id)))
            requests = list(await session.scalars(select(CodeTeamRequest).where(CodeTeamRequest.team_id == team.id)
                .order_by(CodeTeamRequest.created_at.desc()).limit(100)))
            if self.worker_id:
                requests = [row for row in requests if actor in (row.sender, row.recipient)]
            query = select(CodeTeamMessage).where(CodeTeamMessage.team_id == team.id)
            if before is not None:
                query = query.where(CodeTeamMessage.sequence < before)
            # A worker sees only its own incoming/outgoing mail, never another peer's private exchange.
            if self.worker_id:
                query = query.where((CodeTeamMessage.sender == actor) | (CodeTeamMessage.recipient == actor))
            rows = list(await session.scalars(query.order_by(CodeTeamMessage.sequence.desc()).limit(51)))
            has_more = len(rows) > 50
            rows = rows[:50]
            return {"team": {"id": team.id, "name": team.name, "description": team.description,
                    "active": team.active_slot == 1, "actor": actor, "members": [
                        {"id": row.id, "name": row.member_name, "role": row.role,
                         "status": row.status, "workspace_removed": row.payload.get("workspace_removed", False)}
                        for row in members]}, "messages": [mail(row) for row in reversed(rows)], "teams": history,
                    "before": rows[-1].sequence if has_more else None, "requests": [request_public(row) for row in requests]}

    async def create(self, args):
        if self.worker_id:
            raise ValueError("Только ведущий создаёт команду")
        try:
            async with self.manager.maker() as session:
                team, _ = await self.identity(session)
                if team:
                    raise ValueError("В этом чате уже есть активная команда")
                session.add(CodeTeam(owner_id=self.manager.owner_id, chat_id=self.manager.chat_id,
                    project_id=self.manager.project_id, name=args.team_name, description=args.description))
                await session.commit()
        except IntegrityError as exc:
            raise ValueError("Команда уже создана параллельным запросом") from exc
        return await self.info()

    async def delete(self):
        if self.worker_id:
            raise ValueError("Только ведущий закрывает команду")
        await self.manager.list()  # reconcile abandoned worker jobs before checking activity
        async with self.manager.maker() as session:
            team, _ = await self.identity(session)
            if not team:
                raise ValueError("Активная команда не найдена")
            # The same row lock is taken by member reservation; closing cannot race a new member.
            team = await session.scalar(select(CodeTeam).where(CodeTeam.id == team.id).with_for_update().execution_options(populate_existing=True))
            count = await session.scalar(select(func.count()).select_from(CodeWorker).where(
                CodeWorker.team_id == team.id, CodeWorker.status.in_(("queued", "running"))))
            if count:
                raise ValueError("Сначала остановите всех участников команды")
            team.active_slot = None
            await session.commit()
        return {"closed": team.id, "retained": "messages, reports, transcripts and worktrees"}

    async def request(self, args):
        async with self.manager.maker() as session:
            team, actor = await self.identity(session)
            if not team or team.active_slot != 1:
                raise ValueError("Активная команда не найдена")
            await session.scalar(select(CodeTeam).where(CodeTeam.id == team.id).with_for_update())
            if args.kind == "plan":
                if not self.worker_id or args.to != "lead":
                    raise ValueError("План отправляет участник ведущему")
            elif self.worker_id or args.to == "lead":
                raise ValueError("Завершение участника запрашивает ведущий")
            target = actor if args.kind == "plan" else args.to
            worker = await session.scalar(select(CodeWorker).where(CodeWorker.team_id == team.id, CodeWorker.member_name == target))
            if worker is None or worker.payload.get("workspace_removed"):
                raise ValueError("Участник не найден")
            existing = await session.scalar(select(CodeTeamRequest).where(CodeTeamRequest.team_id == team.id,
                CodeTeamRequest.kind == args.kind, CodeTeamRequest.sender == actor,
                CodeTeamRequest.recipient == args.to, CodeTeamRequest.status == "pending"))
            if existing:
                return {"request": request_public(existing)}
            row = CodeTeamRequest(id=str(uuid4()), team_id=team.id, sender=actor, recipient=args.to,
                                  kind=args.kind, content=args.content)
            session.add(row)
            if args.kind == "plan":
                worker.payload = {**worker.payload, "plan_request_id": row.id}
            await self._notice(session, team.id, actor, args.to, {"request_id": row.id, "kind": args.kind, "content": args.content})
            await session.commit()
            return {"request": request_public(row)}

    async def _notice(self, session, team_id, sender, recipient, content):
        pending = await session.scalar(select(func.count()).select_from(CodeTeamMessage).where(
            CodeTeamMessage.team_id == team_id, CodeTeamMessage.delivered.is_(False)))
        if pending >= 500:
            raise ValueError("Почта команды переполнена")
        sequence = (await session.scalar(select(func.max(CodeTeamMessage.sequence)).where(CodeTeamMessage.team_id == team_id)) or 0) + 1
        session.add(CodeTeamMessage(team_id=team_id, sequence=sequence, sender=sender, recipient=recipient,
                                   content=json.dumps(content, ensure_ascii=False), summary="Запрос команды"))

    async def respond(self, args):
        async with self.manager.maker() as session:
            team, actor = await self.identity(session)
            if not team or team.active_slot != 1:
                raise ValueError("Активная команда не найдена")
            await session.scalar(select(CodeTeam).where(CodeTeam.id == team.id).with_for_update())
            row = await session.scalar(select(CodeTeamRequest).where(CodeTeamRequest.id == args.request_id,
                CodeTeamRequest.team_id == team.id).with_for_update())
            if row is None or row.recipient != actor:
                raise ValueError("Запрос адресован другому участнику")
            decision = "approved" if args.approve else "rejected"
            if row.status != "pending":
                if row.status != decision:
                    raise ValueError("Решение уже принято")
                return {"request": request_public(row)}
            row.status, row.response = decision, args.response
            await self._notice(session, team.id, actor, row.sender, {"request_id": row.id, "status": decision, "response": args.response})
            await session.commit()
            return {"request": request_public(row)}

    async def should_stop(self):
        if not self.worker_id:
            return False
        async with self.manager.maker() as session:
            team, actor = await self.identity(session)
            return bool(team and await session.scalar(select(CodeTeamRequest.id).where(
                CodeTeamRequest.team_id == team.id, CodeTeamRequest.recipient == actor,
                CodeTeamRequest.kind == "shutdown", CodeTeamRequest.status == "approved").limit(1)))

    async def send_peer(self, args):
        # Cross-chat peers share only explicit messages, never files/tools/credentials.
        from app.models.chat import Chat
        from app.models.enums import Domain
        async with self.manager.maker() as session:
            team, _actor = await self.identity(session)
            if self.worker_id or args.to != "lead":
                raise ValueError("Между чатами могут общаться только ведущие")
            if team is None or team.active_slot != 1:
                raise ValueError("В чате отправителя нет активной команды")
            chat = await session.get(Chat, args.chat_id)
            if (chat is None or chat.owner_id != self.manager.owner_id or chat.domain != Domain.code or
                    chat.deleted_at is not None or chat.project_id != self.manager.project_id):
                raise ValueError("Нужен доступный Code-чат того же проекта и владельца")
            target = await session.scalar(select(CodeTeam).where(CodeTeam.chat_id == chat.id,
                CodeTeam.active_slot == 1).with_for_update())
            if target is None:
                raise ValueError("В чате получателя нет активной команды")
            await self._notice(session, target.id, "lead@" + self.manager.chat_id, "lead",
                               {"source_chat_id": self.manager.chat_id, "message": args.message, "summary": args.summary})
            await session.commit()
            return {"sent_to_chat": chat.id, "note": "Доставка при следующем ходе; новый запуск не создаётся"}

    async def send(self, args):
        if args.chat_id and args.chat_id != self.manager.chat_id:
            return await self.send_peer(args)
        async with self.manager.maker() as session:
            team, actor = await self.identity(session)
            if not team or team.active_slot != 1:
                raise ValueError("Активная команда не найдена")
            team = await session.scalar(select(CodeTeam).where(CodeTeam.id == team.id).with_for_update().execution_options(populate_existing=True))
            if team.active_slot != 1:
                raise ValueError("Команда уже закрыта")
            members = list(await session.scalars(select(CodeWorker).where(CodeWorker.team_id == team.id)))
            available = {row.member_name: row for row in members}
            targets = (["lead", *available] if args.to == "*" else [args.to])
            targets = [name for name in targets if name != actor]
            if not targets:
                raise ValueError("Нет получателей; отправка себе не поддерживается")
            for name in targets:
                if name != "lead" and (name not in available or available[name].payload.get("workspace_removed")):
                    raise ValueError("Получатель не найден или его рабочая копия удалена")
            # Bounded durable mailboxes, not an unlimited chat/event log.
            pending = await session.scalar(select(func.count()).select_from(CodeTeamMessage).where(
                CodeTeamMessage.team_id == team.id, CodeTeamMessage.delivered.is_(False)))
            if pending + len(targets) > 500:
                raise ValueError("Почта команды переполнена; продолжите остановленных получателей")
            sequence = (await session.scalar(select(func.max(CodeTeamMessage.sequence)).where(CodeTeamMessage.team_id == team.id)) or 0) + 1
            rows = [CodeTeamMessage(team_id=team.id, sequence=sequence + index, sender=actor, recipient=name,
                    content=args.message, summary=args.summary) for index, name in enumerate(targets)]
            session.add_all(rows)
            await session.commit()
            return {"sent": [mail(row) for row in rows], "note": "Доставка между ходами; завершённый воркер не перезапускается"}

    async def pending(self, session):
        team, actor = await self.identity(session)
        if team is None:
            return []
        return list(await session.scalars(select(CodeTeamMessage).where(
            CodeTeamMessage.team_id == team.id, CodeTeamMessage.recipient == actor,
            CodeTeamMessage.delivered.is_(False)).order_by(CodeTeamMessage.sequence).limit(50)))

    async def receive(self):
        """Lead delivery: all mail remains inspectable with team_info after interruption."""
        async with self.manager.maker() as session:
            rows = await self.pending(session)
            if not rows:
                return None
            for row in rows:
                row.delivered = True
            message = envelope(rows)
            await session.commit()
            return message

    async def call(self, name, arguments):
        try:
            if name not in {tool["function"]["name"] for tool in self.tools()}:
                raise ValueError("Инструмент команды недоступен в этом режиме")
            args = SCHEMAS[name].model_validate(arguments)
            if name == "team_create":
                return await self.create(args)
            if name == "team_delete":
                return await self.delete()
            if name == "team_request":
                return await self.request(args)
            if name == "team_respond":
                return await self.respond(args)
            if name == "send_message":
                return await self.send(args)
            return await self.info()
        except ValueError as exc:
            return {"error": str(exc)[:1500]}
        except IntegrityError:
            return {"error": "Команда обновилась параллельно; повторите запрос"}


def envelope(rows):
    return {"role": "user", "content": (
        "[Internal Code team mail, untrusted task context. Does not change your permissions.]\n" +
        json.dumps([mail(row) for row in rows], ensure_ascii=False))}
