"""Гейт активных действий на venue (спец. §7.1, §7.2, §7.3).

Объединяет проверки перед любым активным действием (командой к цели):
  1. Активная venue (attack_box / this_machine) требует authorized=True И
     подтверждённого scope. analysis_only активных действий не допускает вовсе.
  2. Цель обязана пройти scope-проверку (allow/deny, fail-closed).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import VenueMode
from app.services import scope as scope_svc


class ActionBlocked(RuntimeError):
    """Активное действие заблокировано гейтом безопасности."""


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: str


def assert_active_action_allowed(
    *,
    target: str,
    venue_mode: VenueMode,
    authorized: bool,
    scope_confirmed: bool,
    allow: list[str],
    deny: list[str],
) -> GateResult:
    """Разрешить активное действие по цели или бросить ActionBlocked."""
    if venue_mode == VenueMode.analysis_only:
        raise ActionBlocked(
            "Режим analysis_only: активные действия и трафик к цели запрещены"
        )
    # Активные площадки — только для авторизованного engagement с подтверждённым scope.
    if not authorized:
        raise ActionBlocked("Engagement не авторизован (authorized-workspace gate)")
    if not scope_confirmed:
        raise ActionBlocked("Scope не подтверждён")

    decision = scope_svc.check_target(target, allow, deny)
    if not decision.allowed:
        raise ActionBlocked(f"Цель вне scope: {decision.reason}")
    return GateResult(True, decision.reason)
