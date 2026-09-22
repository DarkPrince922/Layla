"""Домен Design: генерация и хранение артефактов (спец. §5.6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, get_sessionmaker
from app.models.design import Design
from app.models.job import Job
from app.models.provider import Provider
from app.models.user import User
from app.schemas.design import DesignCreate, DesignOut
from app.schemas.job import JobOut
from app.services import audit, design_gen, jobs, provider_client
from app.services.auth import get_current_user

router = APIRouter(prefix="/designs", tags=["design"])


async def _pick_model(session: AsyncSession, user: User, requested: str | None) -> str:
    if requested:
        return requested
    from app.api.models import enabled_models

    providers = list(
        await session.scalars(
            select(Provider).where(
                Provider.owner_id == user.id,
                Provider.enabled == True,  # noqa: E712
                Provider.active == True,  # noqa: E712
            )
        )
    )
    for p in providers:
        names = enabled_models(p)
        if names:
            return names[0]
    names = []
    if not names:
        raise HTTPException(status_code=400, detail="Нет активной модели. Настройте провайдера.")
    return names[0]


@router.get("", response_model=list[DesignOut])
async def list_designs(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Design]:
    rows = await session.scalars(
        select(Design).where(Design.owner_id == user.id).order_by(Design.created_at.desc())
    )
    return list(rows)


@router.get("/{design_id}", response_model=DesignOut)
async def get_design(
    design_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Design:
    design = await session.get(Design, design_id)
    if design is None or design.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Дизайн не найден")
    return design


@router.post("", response_model=DesignOut, status_code=201)
async def create_design(
    body: DesignCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Design:
    """Сгенерировать артефакт по брифу и сохранить его."""
    model = await _pick_model(session, user, body.model)
    messages = design_gen.build_prompt(body.brief.model_dump(), body.stack.value)

    provider = await provider_client.resolve_provider(session, user.id, model)
    if provider is None:
        raise HTTPException(status_code=400, detail="Нет активного провайдера для генерации.")
    key = await provider_client.pick_key(session, provider)
    try:
        raw = await provider_client.complete(provider, key, model, messages)
    except Exception as exc:  # ошибка провайдера
        raise HTTPException(status_code=502, detail=f"Ошибка генерации: {exc}") from exc

    html = design_gen.extract_html(raw)
    files = design_gen.to_files(html)

    design = Design(
        owner_id=user.id,
        stack=body.stack,
        brief=body.brief.model_dump(),
        files=files,
    )
    session.add(design)
    await audit.record(session, actor=user.id, action="design.generate", target=body.stack.value)
    await session.commit()
    return design


@router.post("/generate", response_model=JobOut, status_code=202)
async def generate_design_bg(
    body: DesignCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    sessionmaker=Depends(get_sessionmaker),
) -> Job:
    """Запустить генерацию Design в ФОНЕ и сразу вернуть задачу.

    Можно переключиться в другой домен — задача продолжит выполняться, а её
    прогресс/шаги/размышление видны в панели «В работе».
    """
    model = await _pick_model(session, user, body.model)
    provider = await provider_client.resolve_provider(session, user.id, model)
    if provider is None:
        raise HTTPException(status_code=400, detail="Нет активного провайдера для генерации.")

    brief = body.brief.model_dump()
    stack = body.stack
    owner_id = user.id
    provider_id = provider.id
    messages = design_gen.build_prompt(brief, stack.value)
    title = f"Design · {brief.get('artifact_type') or stack.value}"
    job = await jobs.create_job(
        session, owner_id=owner_id, domain="design", kind="design.generate", title=title
    )

    async def worker(h: jobs.JobHandle) -> None:
        await h.step("Составляю бриф", progress=0.1)
        prov = await h.session.get(Provider, provider_id)
        key = await provider_client.pick_key(h.session, prov)
        await h.step("Генерирую разметку", progress=0.25)
        parts: list[str] = []
        started = False
        async for kind, text in provider_client.stream_chat(prov, key, model, messages):
            if kind == "reasoning":
                await h.reason(text)
            else:
                if not started:
                    started = True
                    await h.step("Модель пишет код", progress=0.5)
                parts.append(text)
        await h.reason("", flush=True)
        html = design_gen.extract_html("".join(parts))
        files = design_gen.to_files(html)
        await h.step("Сохраняю артефакт", progress=0.9)
        design = Design(owner_id=owner_id, stack=stack, brief=brief, files=files)
        h.session.add(design)
        await audit.record(h.session, actor=owner_id, action="design.generate", target=stack.value)
        await h.session.commit()
        await h.set_result({"design_id": design.id})
        await h.step("Готово", progress=1.0)

    jobs.launch(sessionmaker, job.id, worker)
    return job


@router.delete("/{design_id}", status_code=204)
async def delete_design(
    design_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    design = await session.get(Design, design_id)
    if design is None or design.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Дизайн не найден")
    await session.delete(design)
    await session.commit()
