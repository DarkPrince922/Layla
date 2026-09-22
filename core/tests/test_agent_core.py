"""Тесты ядра автономного агента: гейт исполнения, HITL, бюджеты (спец. §5.2, §7)."""
from __future__ import annotations

import pytest

from app.models.enums import EgressRoute, VenueMode
from app.services import orchestrator, venue_executor
from app.services.budgets import BudgetExceeded, Budgets, BudgetTracker
from app.services.egress import EgressBlocked
from app.services.venue_gate import ActionBlocked


# ---- Бюджеты ----
def test_budget_tokens_exceeded():
    t = BudgetTracker(Budgets(tokens_per_turn=100))
    t.add(tokens=150)
    with pytest.raises(BudgetExceeded):
        t.check()


def test_budget_cost_exceeded():
    t = BudgetTracker(Budgets(cost_usd=1.0))
    t.add(cost=1.5)
    with pytest.raises(BudgetExceeded):
        t.check()


def test_budget_time_exceeded():
    t = BudgetTracker(Budgets(subagent_minutes=1))
    with pytest.raises(BudgetExceeded):
        t.check(elapsed_seconds=120)


def test_budget_within_limits_ok():
    t = BudgetTracker(Budgets(tokens_per_turn=1000, cost_usd=5))
    t.add(tokens=10, cost=0.01)
    t.check(elapsed_seconds=1)  # не бросает


# ---- Danger classifier ----
def test_dangerous_commands_flagged():
    assert venue_executor.is_dangerous("rm -rf /")
    assert venue_executor.is_dangerous("sqlmap -u x --os-shell")
    assert not venue_executor.is_dangerous("nmap -sV example.com")


# ---- Исполнение строго за гейтами ----
async def _run_ok(cmd: str) -> str:
    return "ok:" + cmd


@pytest.mark.asyncio
async def test_execute_blocked_out_of_scope():
    with pytest.raises(ActionBlocked):
        await venue_executor.execute(
            target="evil.com", command="nmap evil.com", venue_mode=VenueMode.attack_box,
            authorized=True, scope_confirmed=True, allow=["example.com"], deny=[],
            runner=_run_ok,
        )


@pytest.mark.asyncio
async def test_execute_blocked_without_authorization():
    with pytest.raises(ActionBlocked):
        await venue_executor.execute(
            target="example.com", command="nmap example.com", venue_mode=VenueMode.attack_box,
            authorized=False, scope_confirmed=True, allow=["example.com"], deny=[],
            runner=_run_ok,
        )


@pytest.mark.asyncio
async def test_execute_blocked_in_analysis_only():
    with pytest.raises(ActionBlocked):
        await venue_executor.execute(
            target="example.com", command="nmap example.com", venue_mode=VenueMode.analysis_only,
            authorized=True, scope_confirmed=True, allow=["example.com"], deny=[],
            runner=_run_ok,
        )


@pytest.mark.asyncio
async def test_execute_blocked_when_tor_down():
    with pytest.raises(EgressBlocked):
        await venue_executor.execute(
            target="example.com", command="nmap example.com", venue_mode=VenueMode.attack_box,
            authorized=True, scope_confirmed=True, allow=["example.com"], deny=[],
            egress_route=EgressRoute.tor, egress_probe=lambda r: False, runner=_run_ok,
        )


@pytest.mark.asyncio
async def test_execute_runs_when_all_gates_pass():
    res = await venue_executor.execute(
        target="example.com", command="nmap example.com", venue_mode=VenueMode.attack_box,
        authorized=True, scope_confirmed=True, allow=["example.com"], deny=[],
        egress_route=EgressRoute.direct, runner=_run_ok,
    )
    assert res.ok and res.output == "ok:nmap example.com"


@pytest.mark.asyncio
async def test_execute_gate_passes_but_no_executor():
    with pytest.raises(venue_executor.NoExecutorConfigured):
        await venue_executor.execute(
            target="example.com", command="id", venue_mode=VenueMode.attack_box,
            authorized=True, scope_confirmed=True, allow=["example.com"], deny=[],
            runner=None,
        )


# ---- HITL-политика ----
def test_hitl_interactive_command_always_paused():
    step = {"kind": "command", "command": "nmap x"}
    assert orchestrator.needs_hitl(step, mode="interactive", persona_hitl=False, dangerous=False)


def test_hitl_autonomous_dangerous_paused():
    step = {"kind": "command", "command": "rm -rf /"}
    assert orchestrator.needs_hitl(step, mode="autonomous", persona_hitl=False, dangerous=True)


def test_hitl_autonomous_safe_not_paused():
    step = {"kind": "command", "command": "nmap x"}
    assert not orchestrator.needs_hitl(step, mode="autonomous", persona_hitl=False, dangerous=False)


def test_plan_step_never_hitl():
    step = {"kind": "plan", "summary": "recon"}
    assert not orchestrator.needs_hitl(step, mode="interactive", persona_hitl=True, dangerous=True)


# ---- Парсинг плана ----
def test_parse_plan_from_json_block():
    text = 'Вот план:\n[{"role":"explorer","kind":"analysis","summary":"recon"}]'
    steps = orchestrator.parse_plan(text)
    assert len(steps) == 1
    assert steps[0]["role"] == "explorer"
    assert steps[0]["kind"] == "analysis"


def test_parse_plan_bad_input():
    assert orchestrator.parse_plan("не json") == []


def test_resolve_role_model_inherits_lead():
    assert orchestrator.resolve_role_model("explorer", {"explorer": "inherit"}, "gpt-4o") == "gpt-4o"
    assert orchestrator.resolve_role_model("reviewer", {"reviewer": "claude"}, "gpt-4o") == "claude"
