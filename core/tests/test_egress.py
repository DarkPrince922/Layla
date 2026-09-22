"""Тесты egress fail-closed (спец. §7.4, §9 — обязательны)."""
from __future__ import annotations

import pytest

from app.models.enums import EgressRoute
from app.services import egress
from app.services.egress import EgressBlocked


def test_direct_always_available():
    d = egress.assert_egress_available(EgressRoute.direct)
    assert d.allowed is True


def test_tor_blocked_when_probe_fails():
    # Ключевое правило §7.4: сбой Tor → блокировка, НЕ откат на Direct.
    with pytest.raises(EgressBlocked):
        egress.assert_egress_available(EgressRoute.tor, probe=lambda r: False)


def test_proxy_blocked_when_probe_fails():
    with pytest.raises(EgressBlocked):
        egress.assert_egress_available(EgressRoute.proxy, probe=lambda r: False)


def test_tor_allowed_when_probe_ok():
    d = egress.assert_egress_available(EgressRoute.tor, probe=lambda r: True)
    assert d.allowed is True
    assert d.route == EgressRoute.tor


def test_effective_route_inheritance():
    # venue=inherit, server=tor → tor
    assert (
        egress.effective_route(EgressRoute.inherit, EgressRoute.tor, EgressRoute.direct)
        == EgressRoute.tor
    )
    # venue=proxy перекрывает всё
    assert (
        egress.effective_route(EgressRoute.proxy, EgressRoute.tor, EgressRoute.direct)
        == EgressRoute.proxy
    )
    # всё inherit → глобальный дефолт
    assert (
        egress.effective_route(EgressRoute.inherit, EgressRoute.inherit, EgressRoute.tor)
        == EgressRoute.tor
    )


def test_no_fallback_to_direct_on_failure():
    # Явная проверка: при сбое tor результат — исключение, а не Direct.
    try:
        egress.assert_egress_available(EgressRoute.tor, probe=lambda r: False)
        raised = False
    except EgressBlocked:
        raised = True
    assert raised is True
