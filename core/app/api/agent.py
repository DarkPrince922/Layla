"""API автономного/интерактивного Pentest-агента (спец. §5.2, §5.4, §7.8).

Планирование через LLM (Ultracode). Каждый активный шаг (command) проходит
scope/venue/egress-гейт и HITL-подтверждение оператором до исполнения. Реальный
исполнитель на attack box подключается отдельно; без него команда, пройдя гейты,
помечается как заблокированная (нет настроенного исполнителя), а не выполняется.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, get_sessionmaker
from app.models.agent import AgentConfig, AgentRun, AgentStep
from app.models.enums import AgentRunStatus, Domain, EgressRoute
from app.models.job import Job
from app.models.pentest import Engagement, Finding, Scope, Venue
from app.models.persona import Persona
from app.models.provider import Provider
from app.models.user import User
from app.schemas.job import JobOut
from app.schemas.agent import (
    AgentConfigIn,
    AgentConfigOut,
    AgentRunCreate,
    AgentRunOut,
    AgentStepOut,
    TriageRequest,
    TriageResult,
)
from app.services import audit, jobs, orchestrator, provider_client, venue_executor
from app.services.auth import get_current_user
from app.services.venue_gate import ActionBlocked
from app.services.egress import EgressBlocked

router = APIRouter(tags=["agent"])


# ---------- helpers ----------
async def _engagement(session: AsyncSession, user: User, eid: str) -> Engagement:
    e = await session.get(Engagement, eid)
    if e is None or e.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Engagement не найден")
    return e


async def _pick_model(session: AsyncSession, user: User, requested: str | None) -> str:
    if requested:
        return requested
    from app.models.provider import Provider
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
    raise HTTPException(status_code=400, detail="Нет активной модели. Включите модель в настройках провайдера.")


async def _run_out(session: AsyncSession, run: AgentRun) -> AgentRunOut:
    steps = await session.scalars(
        select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.ordinal)
    )
    out = AgentRunOut.model_validate(run)
    out.steps = [AgentStepOut.model_validate(s) for s in steps]
    return out


# ---------- runs ----------
@router.post("/engagements/{eid}/agent/runs", response_model=AgentRunOut, status_code=201)
async def create_run(
    eid: str,
    body: AgentRunCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentRunOut:
    e = await _engagement(session, user, eid)
    model = await _pick_model(session, user, body.model)
    sc = await session.scalar(select(Scope).where(Scope.engagement_id == eid))
    allow = (sc.allow if sc else []) or []

    persona_hitl = True
    if body.persona_id:
        p = await session.get(Persona, body.persona_id)
        persona_hitl = bool(p and p.hitl_required)

    run = AgentRun(
        owner_id=user.id, domain=Domain.pentest, engagement_id=eid,
        task=body.task, mode=body.mode, model=model, persona_id=body.persona_id,
        status=AgentRunStatus.running,
    )
    session.add(run)
    await session.flush()

    # Планирование через LLM.
    provider = await provider_client.resolve_provider(session, user.id, model)
    if provider is None:
        raise HTTPException(status_code=400, detail="Нет активного провайдера для модели")
    key = await provider_client.pick_key(session, provider)
    messages = orchestrator.build_planner_messages(body.task, allow)
    try:
        raw = await provider_client.complete(provider, key, model, messages)
    except Exception as exc:
        run.status = AgentRunStatus.failed
        await session.commit()
        raise HTTPException(status_code=502, detail=f"Ошибка планирования: {exc}") from exc

    plan = orchestrator.parse_plan(raw)
    for step in plan:
        dangerous = venue_executor.is_dangerous(step.get("command"))
        hitl = orchestrator.needs_hitl(
            step, mode=body.mode, persona_hitl=persona_hitl, dangerous=dangerous
        )
        if step["kind"] == "plan":
            status = "done"
        elif step["kind"] == "command":
            status = "awaiting_approval"
        else:
            status = "ready"
        session.add(
            AgentStep(
                run_id=run.id, ordinal=step["ordinal"], role=step["role"], kind=step["kind"],
                status=status, requires_hitl=hitl, target=step.get("target"),
                command=step.get("command"), summary=step.get("summary"),
            )
        )
    run.status = AgentRunStatus.paused if any(
        s["kind"] == "command" for s in plan
    ) else AgentRunStatus.completed
    await audit.record(session, actor=user.id, action="agent.run.create", target=eid,
                       meta={"steps": len(plan), "mode": body.mode})
    await session.commit()
    return await _run_out(session, run)


@router.get("/engagements/{eid}/agent/runs", response_model=list[AgentRunOut])
async def list_runs(
    eid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AgentRunOut]:
    await _engagement(session, user, eid)
    runs = await session.scalars(
        select(AgentRun).where(AgentRun.engagement_id == eid).order_by(AgentRun.created_at.desc())
    )
    return [await _run_out(session, r) for r in runs]


@router.get("/agent/runs/{rid}", response_model=AgentRunOut)
async def get_run(
    rid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentRunOut:
    run = await session.get(AgentRun, rid)
    if run is None or run.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Run не найден")
    return await _run_out(session, run)


async def _owned_step(session: AsyncSession, user: User, sid: str) -> tuple[AgentRun, AgentStep]:
    step = await session.get(AgentStep, sid)
    if step is None:
        raise HTTPException(status_code=404, detail="Шаг не найден")
    run = await session.get(AgentRun, step.run_id)
    if run is None or run.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Шаг не найден")
    return run, step


@router.post("/agent/steps/{sid}/deny", response_model=AgentStepOut)
async def deny_step(
    sid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentStepOut:
    run, step = await _owned_step(session, user, sid)
    step.status = "denied"
    await audit.record(session, actor=user.id, action="agent.step.deny", target=step.id)
    await session.commit()
    return AgentStepOut.model_validate(step)


@router.post("/agent/steps/{sid}/approve", response_model=AgentStepOut)
async def approve_step(
    sid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentStepOut:
    """Подтвердить и выполнить шаг. Команда проходит scope/venue/egress-гейт."""
    run, step = await _owned_step(session, user, sid)
    if step.kind != "command" or not step.command:
        # analysis-шаг: безопасный LLM-анализ, без активных действий.
        step.status = "done"
        step.output = step.summary or "Проанализировано"
        await session.commit()
        return AgentStepOut.model_validate(step)

    e = await session.get(Engagement, run.engagement_id)
    sc = await session.scalar(select(Scope).where(Scope.engagement_id == run.engagement_id))
    vn = await session.scalar(select(Venue).where(Venue.engagement_id == run.engagement_id))
    if e is None or vn is None:
        raise HTTPException(status_code=400, detail="Engagement/venue не найдены")

    step.status = "running"
    try:
        await venue_executor.execute(
            target=step.target or e.target,
            command=step.command,
            venue_mode=vn.mode,
            authorized=e.authorized,
            scope_confirmed=bool(sc and sc.confirmed),
            allow=(sc.allow if sc else []) or [],
            deny=(sc.deny if sc else []) or [],
            egress_route=vn.egress_route or EgressRoute.inherit,
            runner=None,  # реальный исполнитель attack box не подключён
        )
        step.status = "done"
    except ActionBlocked as exc:
        step.status = "blocked"
        step.output = f"Заблокировано гейтом: {exc}"
    except EgressBlocked as exc:
        step.status = "blocked"
        step.output = f"Egress заблокирован: {exc}"
    except venue_executor.NoExecutorConfigured as exc:
        step.status = "blocked"
        step.output = f"{exc}"
    await audit.record(
        session, actor=user.id, action="agent.step.approve", target=step.id,
        meta={"status": step.status, "target": step.target},
    )
    await session.commit()
    return AgentStepOut.model_validate(step)


@router.post("/agent/runs/{rid}/stop", response_model=AgentRunOut)
async def stop_run(
    rid: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentRunOut:
    run = await session.get(AgentRun, rid)
    if run is None or run.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Run не найден")
    run.status = AgentRunStatus.completed
    await session.commit()
    return await _run_out(session, run)


# ---------- триаж находки ----------
@router.post("/engagements/{eid}/findings/{fid}/triage", response_model=TriageResult)
async def triage_finding(
    eid: str,
    fid: str,
    body: TriageRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TriageResult:
    """Безопасный анализ находки моделью (без активных действий)."""
    await _engagement(session, user, eid)
    f = await session.get(Finding, fid)
    if f is None or f.engagement_id != eid:
        raise HTTPException(status_code=404, detail="Находка не найдена")
    model = await _pick_model(session, user, body.model)
    provider = await provider_client.resolve_provider(session, user.id, model)
    if provider is None:
        raise HTTPException(status_code=400, detail="Нет активного провайдера")
    key = await provider_client.pick_key(session, provider)
    sev = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
    messages = [
        {"role": "system", "content": "Ты — аналитик безопасности. Кратко оцени находку: "
         "вероятность истинного срабатывания, влияние и следующий безопасный шаг проверки. "
         "Без активных действий."},
        {"role": "user", "content": f"Находка: [{sev}] {f.type}\nURL: {f.method or ''} {f.url or ''}\n"
         f"Параметр: {f.param or '-'}\nОписание: {f.description or '-'}"},
    ]
    try:
        verdict = await provider_client.complete(provider, key, model, messages)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка триажа: {exc}") from exc
    await audit.record(session, actor=user.id, action="finding.triage", target=fid)
    return TriageResult(finding_id=fid, verdict=verdict.strip())


@router.post("/engagements/{eid}/findings/{fid}/triage/bg", response_model=JobOut, status_code=202)
async def triage_finding_bg(
    eid: str,
    fid: str,
    body: TriageRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    maker=Depends(get_sessionmaker),
) -> Job:
    """Триаж находки в ФОНЕ: вердикт и размышление модели видны в панели «В работе»."""
    await _engagement(session, user, eid)
    f = await session.get(Finding, fid)
    if f is None or f.engagement_id != eid:
        raise HTTPException(status_code=404, detail="Находка не найдена")
    model = await _pick_model(session, user, body.model)
    provider = await provider_client.resolve_provider(session, user.id, model)
    if provider is None:
        raise HTTPException(status_code=400, detail="Нет активного провайдера")
    provider_id = provider.id
    sev = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
    messages = [
        {"role": "system", "content": "Ты — аналитик безопасности. Кратко оцени находку: "
         "вероятность истинного срабатывания, влияние и следующий безопасный шаг проверки. "
         "Без активных действий."},
        {"role": "user", "content": f"Находка: [{sev}] {f.type}\nURL: {f.method or ''} {f.url or ''}\n"
         f"Параметр: {f.param or '-'}\nОписание: {f.description or '-'}"},
    ]
    owner_id = user.id
    job = await jobs.create_job(
        session, owner_id=owner_id, domain="pentest", kind="triage",
        title=f"Триаж · {f.type}",
    )

    async def worker(h: jobs.JobHandle) -> None:
        prov = await h.session.get(Provider, provider_id)
        key = await provider_client.pick_key(h.session, prov)
        await h.step("Анализирую находку", progress=0.3)
        parts: list[str] = []
        async for kind, text in provider_client.stream_chat(prov, key, model, messages):
            if kind == "reasoning":
                await h.reason(text)
            else:
                parts.append(text)
        await h.reason("", flush=True)
        verdict = "".join(parts).strip()
        await h.set_result({"finding_id": fid, "verdict": verdict})
        await audit.record(h.session, actor=owner_id, action="finding.triage", target=fid)
        await h.session.commit()
        await h.step("Готово", progress=1.0)

    jobs.launch(maker, job.id, worker)
    return job


# ---------- настройки Ultracode ----------
@router.get("/agent/config", response_model=AgentConfigOut)
async def get_config(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentConfigOut:
    cfg = await session.scalar(select(AgentConfig).where(AgentConfig.owner_id == user.id))
    if cfg is None:
        return AgentConfigOut()
    return AgentConfigOut(
        preset=cfg.preset, role_models=cfg.role_models or {}, budgets=cfg.budgets or {},
        diagnostics=cfg.diagnostics or {}, context=cfg.context or {},
    )


@router.put("/agent/config", response_model=AgentConfigOut)
async def set_config(
    body: AgentConfigIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentConfigOut:
    cfg = await session.scalar(select(AgentConfig).where(AgentConfig.owner_id == user.id))
    if cfg is None:
        cfg = AgentConfig(owner_id=user.id)
        session.add(cfg)
    cfg.preset = body.preset
    cfg.role_models = body.role_models
    cfg.budgets = body.budgets
    cfg.diagnostics = body.diagnostics
    cfg.context = body.context
    await session.commit()
    return AgentConfigOut(**body.model_dump())
