"""Ultracode-оркестрация суб-агентов (спец. §5.2).

Роутер декомпозирует задачу на шаги с ролями (Explorer/Reviewer/Implementer)
через LLM и решает, какие шаги требуют HITL-подтверждения. Само исполнение
активных шагов идёт через venue_executor (за гейтами). Здесь — чистые функции
планирования и политики HITL, чтобы их можно было тестировать без сети.
"""
from __future__ import annotations

import json
import re

from app.services.pentest_policy import PENTEST_SYSTEM

ROLES = ("explorer", "reviewer", "implementer")

_PLANNER_SYSTEM = (
    "Ты — ведущий агент авторизованного пентеста (Ultracode). Разбей задачу на "
    "конкретные шаги. Верни ТОЛЬКО JSON-массив объектов вида "
    '{"role":"explorer|reviewer|implementer","kind":"plan|analysis|command",'
    '"target":"<домен/ip или пусто>","command":"<команда или пусто>",'
    '"summary":"<кратко что и зачем>"}. Никаких активных действий вне переданного '
    "scope. Разведка и анализ предпочтительнее деструктивных команд."
)


def build_planner_messages(task: str, scope_allow: list[str], *, scope_deny: list[str] | None = None,
                           offensive_enabled: bool = False, authorized: bool = False,
                           scope_confirmed: bool = False) -> list[dict]:
    scope_txt = ", ".join(scope_allow) if scope_allow else "(scope пуст — только анализ)"
    return [
        {"role": "system", "content": PENTEST_SYSTEM + "\n" + _PLANNER_SYSTEM + "\nТекущее состояние бэкенда: " + json.dumps({
            "allow": scope_allow, "deny": scope_deny or [], "authorized": authorized,
            "scope_confirmed": scope_confirmed, "offensive_enabled": offensive_enabled,
        }, ensure_ascii=False) + "\nЕсли любое разрешение отсутствует, предлагай только plan/analysis; не создавай command. Формат JSON обязателен; отчёт оформляется отдельной задачей после выполнения и проверки результатов."},
        {"role": "user", "content": f"Разрешённый scope: {scope_txt}\nЗадача: {task}"},
    ]


def parse_plan(text: str) -> list[dict]:
    """Извлечь план (список шагов) из ответа модели; устойчиво к обрамлению."""
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    raw = m.group(0) if m else (text or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    steps: list[dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        role = item.get("role", "lead")
        if role not in ROLES and role != "lead":
            role = "lead"
        kind = item.get("kind", "plan")
        if kind not in ("plan", "analysis", "command", "triage"):
            kind = "plan"
        steps.append(
            {
                "ordinal": i,
                "role": role,
                "kind": kind,
                "target": (item.get("target") or "").strip() or None,
                "command": (item.get("command") or "").strip() or None,
                "summary": (item.get("summary") or "").strip(),
            }
        )
    return steps


def needs_hitl(step: dict, *, mode: str, persona_hitl: bool, dangerous: bool) -> bool:
    """Нужна ли пауза на подтверждение оператором (спец. §7.8).

    * Активная команда в interactive-режиме — всегда HITL.
    * В autonomous-режиме — HITL для опасных команд или если персона требует HITL.
    * Планы/анализ (без команды) — без HITL.
    """
    if step.get("kind") != "command" or not step.get("command"):
        return False
    if mode == "interactive":
        return True
    return dangerous or persona_hitl


def resolve_role_model(role: str, role_models: dict, lead_model: str) -> str:
    """Модель для роли: назначенная или наследует lead-модель."""
    val = (role_models or {}).get(role)
    if not val or val == "inherit":
        return lead_model
    return val
