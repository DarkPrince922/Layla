"""Immutable Code plan submissions; user approval is bound to one execution."""
from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.models.chat import Message
from app.models.code_control import CodePlan
from app.models.code_workspace import CodeWorkspace


class Submit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    plan: str = Field(min_length=1, max_length=30000)


class Enter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)


SCHEMAS = {"enter_plan_mode": Enter, "submit_plan": Submit}
TOOLS = [{"type": "function", "function": {"name": name, "parameters": schema.model_json_schema(),
    "description": ("Pause execution and switch THIS Code lead into read-only Plan mode. Finish by submit_plan; only the user can approve execution."
                    if name == "enter_plan_mode" else
                    "Submit an immutable implementation plan for user approval, then stop. Include files, concrete steps, risks and validation. This never grants execution permission.")}}
    for name, schema in SCHEMAS.items()]


async def workspace_id(session, runtime):
    return await session.scalar(select(CodeWorkspace.id).where(CodeWorkspace.chat_id == runtime.chat_id,
        CodeWorkspace.owner_id == runtime.owner_id, CodeWorkspace.project_id == runtime.project_id,
        CodeWorkspace.active_slot == 1))


def public(row):
    return {key: getattr(row, key) for key in ("id", "title", "content", "status", "feedback",
            "message_id", "workspace_id", "mode", "execution_id")}


async def submit(session, runtime, message_id, title, content):
    await runtime._authorize(session)
    args = Submit(title=title, plan=content)
    if not args.title.strip() or not args.plan.strip():
        raise ValueError("План не может быть пустым")
    existing = await session.scalar(select(CodePlan).where(CodePlan.message_id == message_id))
    if existing:
        return existing
    message = await session.get(Message, message_id)
    if message is None or message.chat_id != runtime.chat_id or message.role != "assistant":
        raise ValueError("Сообщение плана недоступно")
    for previous in await session.scalars(select(CodePlan).where(CodePlan.chat_id == runtime.chat_id,
            CodePlan.status == "pending")):
        previous.status = "superseded"
    plan = CodePlan(owner_id=runtime.owner_id, chat_id=runtime.chat_id, project_id=runtime.project_id,
        workspace_id=await workspace_id(session, runtime), message_id=message_id,
        title=title, content=content, status="pending")
    session.add(plan)
    await session.flush()
    return plan


async def approve(session, runtime, plan_id, mode, execution_id):
    await runtime._authorize(session)
    plan = await session.scalar(select(CodePlan).where(CodePlan.id == plan_id,
        CodePlan.owner_id == runtime.owner_id, CodePlan.chat_id == runtime.chat_id,
        CodePlan.project_id == runtime.project_id).with_for_update())
    if plan is None or plan.status != "pending" or mode not in ("auto", "confirm"):
        raise ValueError("План недоступен для утверждения или уже был выполнен")
    message = await session.get(Message, plan.message_id)
    if (message.meta or {}).get("disabled") or (message.meta or {}).get("rolled_back"):
        raise ValueError("План отменён откатом")
    if plan.workspace_id != await workspace_id(session, runtime):
        raise ValueError("Рабочая копия изменилась: вернитесь к копии плана или составьте новый")
    newer = list(await session.scalars(select(Message).where(Message.chat_id == runtime.chat_id,
        Message.role == "user", Message.created_at > message.created_at)))
    if any(not (row.meta or {}).get("disabled") for row in newer):
        raise ValueError("После плана появились новые указания: сначала обновите план")
    return plan


async def finish(runtime, execution_id, failed):
    async with runtime.maker() as session:
        await runtime._authorize(session)
        row = await session.scalar(select(CodePlan).where(CodePlan.chat_id == runtime.chat_id,
            CodePlan.execution_id == execution_id, CodePlan.status == "executing"))
        if row:
            row.status = "interrupted" if failed else "completed"
            row.updated_at = datetime.now(UTC)
            await session.commit()


async def context(runtime):
    async with runtime.maker() as session:
        await runtime._authorize(session)
        rows = list(await session.scalars(select(CodePlan).where(CodePlan.chat_id == runtime.chat_id,
            CodePlan.project_id == runtime.project_id).order_by(CodePlan.created_at.desc()).limit(3)))
        return [{"id": r.id, "status": r.status, "plan": r.content, "feedback": r.feedback} for r in rows]


async def reconcile(session, row):
    if row.status == "executing" and row.execution_id:
        message = await session.get(Message, row.execution_id)
        if message is None or (message.meta or {}).get("error"):
            row.status = "interrupted"


async def pending(runtime):
    async with runtime.maker() as session:
        await runtime._authorize(session)
        return await session.scalar(select(CodePlan.id).where(CodePlan.chat_id == runtime.chat_id,
            CodePlan.project_id == runtime.project_id, CodePlan.status == "pending").limit(1))
