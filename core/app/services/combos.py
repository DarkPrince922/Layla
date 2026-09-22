"""Combos router: маршрутизация между моделями с per-model lockout (спец. §5.1).

Несколько моделей-кандидатов образуют combo. Роутер выбирает первую здоровую
модель по приоритету; при сбое (rate limit / ошибка) модель уводится в lockout
на cooldown (circuit breaker) и по истечении восстанавливается. Логика чистая и
тестируемая; time-провайдер инъектируется.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ModelState:
    name: str
    locked_until: float = 0.0
    failures: int = 0


class ModelRouter:
    def __init__(
        self,
        models: list[str],
        *,
        cooldown_seconds: float = 60.0,
        failure_threshold: int = 2,
        now=time.monotonic,
    ) -> None:
        self._models = [ModelState(m) for m in models]
        self._cooldown = cooldown_seconds
        self._threshold = failure_threshold
        self._now = now

    def _healthy(self) -> list[ModelState]:
        t = self._now()
        for m in self._models:
            if m.locked_until and t >= m.locked_until:
                m.locked_until = 0.0
                m.failures = 0
        return [m for m in self._models if not m.locked_until]

    def pick(self) -> str | None:
        """Выбрать первую здоровую модель по приоритету (или None)."""
        healthy = self._healthy()
        return healthy[0].name if healthy else None

    def report_success(self, name: str) -> None:
        for m in self._models:
            if m.name == name:
                m.failures = 0
                return

    def report_failure(self, name: str, *, rate_limited: bool = False) -> None:
        """Инкремент ошибок; при пороге (или сразу при rate limit) — lockout."""
        for m in self._models:
            if m.name == name:
                m.failures += 1
                if rate_limited or m.failures >= self._threshold:
                    m.locked_until = self._now() + self._cooldown
                return

    def status(self) -> list[dict]:
        t = self._now()
        return [
            {
                "name": m.name,
                "locked": bool(m.locked_until and t < m.locked_until),
                "failures": m.failures,
            }
            for m in self._models
        ]
