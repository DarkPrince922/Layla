"""Домен Design: генерация и хранение артефактов (спец. §5.6)."""
from __future__ import annotations

import math
import shutil
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import get_session, get_sessionmaker
from app.models.design import Design
from app.models.job import Job
from app.models.provider import Provider
from app.models.user import Project, User, Workspace
from app.schemas.design import DesignCreate, DesignOut
from app.schemas.job import JobOut
from app.schemas.project import ProjectOut
from app.services import audit, design_gen, jobs, project_agent, provider_client
from app.services.auth import get_current_user
from app.services.files import change_file

router = APIRouter(prefix="/designs", tags=["design"])


DRAFT_INTERVAL = 0.5  # как часто сохранять черновик для живого просмотра, с


async def _push_draft(h: jobs.JobHandle, text: str) -> None:
    # Прогресс по объёму: итоговая длина заранее неизвестна, поэтому кривая с насыщением.
    progress = 0.3 + 0.6 * (1 - math.exp(-len(text) / 24000))
    h.job.progress = round(progress, 3)
    await h.set_result({"draft": text})


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
    title = f"Дизайн · {brief.get('artifact_type') or stack.value}"
    job = await jobs.create_job(
        session, owner_id=owner_id, domain="design", kind="design.generate", title=title
    )

    async def worker(h: jobs.JobHandle) -> None:
        await h.step("Составляю бриф", progress=0.05)
        prov = await h.session.get(Provider, provider_id)
        key = await provider_client.pick_key(h.session, prov)
        # Настройки модели из «Провайдеры» (длина ответа, температура, размышления…)
        # действуют и здесь; креативность брифа задаёт температуру этой генерации.
        caps = design_gen.design_caps((prov.model_caps or {}).get(model), brief)
        await h.set_result({"model": model, "draft": ""})
        await h.step(f"Генерирую макет · {model}", progress=0.1)
        text = ""
        draft_at = reason_at = 0.0  # когда черновик и размышления последний раз ушли в БД
        started = False
        async for kind, value in project_agent._turn(prov, key, model, list(messages), [], caps):
            if kind == "delta":
                text += value
                if not started:
                    started = True
                    await h.step("Модель пишет код", progress=0.3)
                now = time.monotonic()
                if now - draft_at >= DRAFT_INTERVAL:
                    # Черновик виден в «Дизайне» по ходу генерации: код и превью.
                    draft_at = now
                    await _push_draft(h, text)
            elif kind == "retract":
                # Ход повторяется с начала — уже показанный кусок убираем.
                text = text[: max(0, len(text) - value)]
                await _push_draft(h, text)
            elif kind == "reasoning":
                # Размышления тоже видны по ходу, а не пачками по 300 символов.
                now = time.monotonic()
                flush = now - reason_at >= DRAFT_INTERVAL
                if flush:
                    reason_at = now
                await h.reason(value, flush=flush)
            elif kind == "retry":
                await h.step(f"Нет связи с моделью — повтор {value['attempt']} из {value['max']} "
                             f"через {value['delay']:g} с")
            elif kind == "learned":
                # Запоминаем, чего модель не умеет, — так же, как в чатах.
                known = dict(prov.model_caps or {})
                known[model] = {**(known.get(model) or {}), **value}
                prov.model_caps = known
                await h.session.commit()
                await h.step(project_agent.learned_label(value))
            elif kind == "done":
                text = "".join(value[0])
        await h.reason("", flush=True)
        truncated = text.endswith(project_agent.LENGTH_NOTE)
        if truncated:
            text = text[: -len(project_agent.LENGTH_NOTE)]
            await h.step("Макет упёрся в предельную длину ответа — сохраняю то, что успело сгенерироваться")
        html = design_gen.extract_html(text)
        if not html:
            raise RuntimeError("Модель не вернула разметку. Попробуйте ещё раз или другую модель.")
        files = design_gen.to_files(html)
        await h.step("Сохраняю артефакт", progress=0.95)
        design = Design(owner_id=owner_id, stack=stack, brief=brief, files=files)
        h.session.add(design)
        await audit.record(h.session, actor=owner_id, action="design.generate", target=stack.value)
        await h.session.commit()
        await h.set_result({"design_id": design.id, "draft": None, "truncated": truncated})
        await h.step("Готово", progress=1.0)

    jobs.launch(sessionmaker, job.id, worker)
    return job


@router.post("/{design_id}/project", response_model=ProjectOut, status_code=201)
async def design_to_project(
    design_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Project:
    """Передать макет в разработку: файлы макета становятся проектом домена «Код».

    Дальше с ним работает обычный кодинг-агент — можно развивать макет в сайт.
    Повторный вызов возвращает уже созданный проект, а не плодит копии.
    """
    design = await session.get(Design, design_id)
    if design is None or design.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Дизайн не найден")
    if design.project_id:
        existing = await session.get(Project, design.project_id)
        if existing is not None and existing.deleted_at is None:
            return existing  # проект из корзины не возвращаем — создадим новый

    workspace = await session.scalar(
        select(Workspace).where(Workspace.owner_id == user.id).limit(1)
    )
    if workspace is None:
        raise HTTPException(status_code=400, detail="Нет рабочего пространства")

    project_id = str(uuid4())
    base = Path(workspace.projects_dir or get_settings().projects_dir).resolve()
    dest = base / project_id
    await run_in_threadpool(lambda: dest.mkdir(parents=True, mode=0o755))
    try:
        for item in design.files or []:
            name = str(item.get("name") or "index.html")
            await run_in_threadpool(change_file, dest, name, item.get("content") or "", None)
        brief = design.brief or {}
        name = f"Дизайн · {brief.get('artifact_type') or design.stack.value}"
        project = Project(id=project_id, workspace_id=workspace.id, name=name[:200], path=str(dest))
        session.add(project)
        await session.flush()  # проект в БД раньше ссылки на него (внешний ключ в Postgres)
        design.project_id = project_id
        await audit.record(session, actor=user.id, action="design.to_project", target=design_id)
        await session.commit()
    except HTTPException:
        raise
    except Exception as exc:  # каталог создали мы — не оставляем мусор на диске
        await session.rollback()
        await run_in_threadpool(lambda: shutil.rmtree(dest, ignore_errors=True))
        raise HTTPException(status_code=400, detail="Не удалось создать проект из макета") from exc
    return project


@router.delete("", status_code=204)
async def clear_designs(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Удалить все версии пользователя. Проекты, созданные из них, остаются."""
    for design in list(await session.scalars(select(Design).where(Design.owner_id == user.id))):
        await session.delete(design)
    await audit.record(session, actor=user.id, action="design.clear", target=user.id)
    await session.commit()


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
