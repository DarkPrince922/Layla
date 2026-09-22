"""Исполнение действий агента на venue строго за гейтами (спец. §7).

Перед КАЖДЫМ активным действием (команда к цели):
  1. venue_gate: активная площадка + authorized + подтверждённый scope + цель в scope;
  2. egress: маршрут разрешён (Tor/Proxy fail-closed);
затем действие выполняется инъектируемым runner'ом (по умолчанию исполнителя нет —
это каркас; реальный SSH-исполнитель на attack box подключается явно).
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.models.enums import EgressRoute, VenueMode
from app.services import egress as egress_svc
from app.services import venue_gate

# Эвристика «опасных» действий → всегда требуют HITL (спец. §7.8).
_DANGEROUS = [
    r"\brm\s+-rf\b", r"\bmkfs\b", r"\bdd\s+if=", r"\b:\(\)\{", r"\bshutdown\b",
    r"\breboot\b", r"--os-shell", r"--os-pwn", r"\bmsfconsole\b", r"\bexploit\b",
    r"\bhydra\b", r"\bnc\b.*-e", r">\s*/dev/", r"\bchmod\s+777\b", r"\bcurl\b.*\|\s*sh",
    r"\bwget\b.*\|\s*sh", r"DROP\s+TABLE", r"DELETE\s+FROM",
]
_DANGEROUS_RE = re.compile("|".join(_DANGEROUS), re.IGNORECASE)


def is_dangerous(command: str | None) -> bool:
    return bool(command and _DANGEROUS_RE.search(command))


class NoExecutorConfigured(RuntimeError):
    pass


@dataclass
class ExecResult:
    ok: bool
    output: str
    route: str


async def execute(
    *,
    target: str,
    command: str,
    venue_mode: VenueMode,
    authorized: bool,
    scope_confirmed: bool,
    allow: list[str],
    deny: list[str],
    egress_route: EgressRoute = EgressRoute.inherit,
    global_egress: EgressRoute = EgressRoute.direct,
    egress_probe: Callable[[EgressRoute], bool] | None = None,
    runner: Callable[[str], Awaitable[str]] | None = None,
) -> ExecResult:
    """Выполнить активное действие после всех проверок безопасности.

    Бросает ActionBlocked / EgressBlocked при нарушении гейтов;
    NoExecutorConfigured, если реальный исполнитель не подключён.
    """
    # 1. Гейт scope/venue/authorized (analysis_only → блок активных действий).
    venue_gate.assert_active_action_allowed(
        target=target, venue_mode=venue_mode, authorized=authorized,
        scope_confirmed=scope_confirmed, allow=allow, deny=deny,
    )
    # 2. Egress fail-closed.
    route = egress_svc.effective_route(egress_route, None, global_egress)
    egress_svc.assert_egress_available(route, probe=egress_probe)
    # 3. Выполнение.
    if runner is None:
        raise NoExecutorConfigured(
            "Исполнитель не подключён: команда прошла гейты, но attack box не настроен"
        )
    output = await runner(command)
    return ExecResult(ok=True, output=output, route=route.value)
