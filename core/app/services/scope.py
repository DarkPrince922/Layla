"""Жёсткое ограничение области действия (scope enforcement, спец. §7.2).

КАЖДАЯ активная цель (исходящий запрос агента, команда на attack box) проверяется
против allow-list ДО выполнения. Правила (fail-closed):

* Пустой allow-list → запрещено всё (никаких «действий по умолчанию»).
* Любое совпадение в deny → блокировка (deny имеет приоритет над allow).
* Разрешено только то, что совпало с allow и не совпало с deny.

Поддерживаются: точный домен, wildcard-домен (``*.example.com`` и суффикс
``.example.com``), одиночный IP, CIDR-сеть, URL (берётся его host).
Обходов scope в продукте нет и быть не должно (спец. §7).
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ScopeDecision:
    allowed: bool
    reason: str
    matched: str | None = None


def extract_host(target: str) -> str:
    """Достать host из URL/host:port/голого домена или IP."""
    t = (target or "").strip()
    if "://" in t:
        parsed = urlparse(t)
        host = parsed.hostname or ""
    else:
        # host[:port][/path]
        host = t.split("/", 1)[0].split(":", 1)[0]
    return host.strip().lower().rstrip(".")


def _as_ip(value: str):
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _matches_rule(host: str, rule: str) -> bool:
    """Совпадает ли host с одним правилом allow/deny."""
    rule = (rule or "").strip().lower().rstrip(".")
    if not rule or not host:
        return False

    rule_host = extract_host(rule) if "://" in rule else rule

    # CIDR-сеть.
    if "/" in rule_host:
        try:
            net = ipaddress.ip_network(rule_host, strict=False)
        except ValueError:
            net = None
        if net is not None:
            ip = _as_ip(host)
            return ip is not None and ip in net

    # Точный IP.
    rip = _as_ip(rule_host)
    if rip is not None:
        hip = _as_ip(host)
        return hip is not None and hip == rip

    # Wildcard / суффикс-домен.
    if rule_host.startswith("*."):
        suffix = rule_host[1:]  # ".example.com"
        return host == suffix[1:] or host.endswith(suffix)
    if rule_host.startswith("."):
        return host == rule_host[1:] or host.endswith(rule_host)

    # Точный домен либо любой его поддомен.
    return host == rule_host or host.endswith("." + rule_host)


def check_target(target: str, allow: list[str], deny: list[str]) -> ScopeDecision:
    """Решение по цели: разрешена ли она для активных действий."""
    host = extract_host(target)
    if not host:
        return ScopeDecision(False, "Пустая или некорректная цель")

    for rule in deny or []:
        if _matches_rule(host, rule):
            return ScopeDecision(False, f"Цель в deny-list ({rule})", matched=rule)

    if not allow:
        return ScopeDecision(False, "allow-list пуст — по умолчанию запрещено (fail-closed)")

    for rule in allow:
        if _matches_rule(host, rule):
            return ScopeDecision(True, "В области действия", matched=rule)

    return ScopeDecision(False, "Цель вне allow-list")
