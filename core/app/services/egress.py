"""Egress-маршрутизация с fail-closed (спец. §7.4).

Правило: если выбранный маршрут — Tor или Proxy и он недоступен, трафик
**блокируется**, а НЕ откатывается на Direct. Это защита от непреднамеренного
раскрытия источника при отказе анонимизирующего маршрута.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.models.enums import EgressRoute


class EgressBlocked(RuntimeError):
    """Маршрут Tor/Proxy недоступен — трафик заблокирован (fail-closed)."""


@dataclass(frozen=True)
class EgressDecision:
    route: EgressRoute
    allowed: bool
    reason: str


def effective_route(
    venue_route: EgressRoute,
    server_route: EgressRoute | None = None,
    global_default: EgressRoute = EgressRoute.direct,
) -> EgressRoute:
    """Разрешить фактический маршрут с учётом наследования (inherit).

    Порядок: маршрут venue → маршрут сервера → глобальный Privacy Chain.
    global_default сам никогда не бывает inherit (это конкретный профиль).
    """
    for r in (venue_route, server_route):
        if r is not None and r != EgressRoute.inherit:
            return r
    if global_default == EgressRoute.inherit:
        return EgressRoute.direct
    return global_default


def assert_egress_available(
    route: EgressRoute,
    probe: Callable[[EgressRoute], bool] | None = None,
) -> EgressDecision:
    """Проверить доступность маршрута; для Tor/Proxy — fail-closed.

    ``probe`` возвращает True, если маршрут исправен. Для Direct проба не нужна.
    Бросает EgressBlocked, если анонимизирующий маршрут недоступен.
    """
    if route in (EgressRoute.direct, EgressRoute.inherit):
        return EgressDecision(route, True, "Прямой маршрут")

    ok = True if probe is None else bool(probe(route))
    if not ok:
        raise EgressBlocked(
            f"Маршрут {route.value} недоступен — трафик заблокирован "
            f"(fail-closed, отката на Direct нет)"
        )
    return EgressDecision(route, True, f"Маршрут {route.value} исправен")


def tcp_probe(host: str, port: int, timeout: float = 3.0) -> bool:
    """Проверить TCP-доступность (для проб Tor/Proxy). True, если порт открыт."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
