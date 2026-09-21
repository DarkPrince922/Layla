"""Ротация ключей провайдера с circuit breaker (спец. §5.1).

Чистая, тестируемая логика: несколько ключей одного провайдера ротируются
round-robin среди «здоровых»; при rate-limit / исчерпании квоты ключ уводится
в cooldown и временно исключается, а по истечении cooldown — восстанавливается.

Эта логика — источник правды Layla по здоровью ключей. Она также определяет,
какие ключи попадают в конфиг LiteLLM (см. services/litellm.py), где второй
уровень ротации/ fallback выполняет сам роутер LiteLLM.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.models.enums import KeyStatus


@dataclass
class KeyState:
    key_id: str
    secret: str
    status: KeyStatus = KeyStatus.active
    cooldown_until: float = 0.0
    failures: int = 0


class KeyRotator:
    """Round-robin ротатор с cooldown-based circuit breaker.

    Параметры инъектируемы (в т.ч. ``now``), чтобы поведение было детерминированным
    в тестах.
    """

    def __init__(
        self,
        keys: list[KeyState],
        *,
        cooldown_seconds: float = 60.0,
        failure_threshold: int = 3,
        now=time.monotonic,
    ) -> None:
        self._keys = keys
        self._cooldown = cooldown_seconds
        self._failure_threshold = failure_threshold
        self._now = now
        self._cursor = 0

    def _refresh(self) -> None:
        """Вернуть в строй ключи, у которых истёк cooldown."""
        t = self._now()
        for k in self._keys:
            if k.status in (KeyStatus.rate_limited, KeyStatus.exhausted) and t >= k.cooldown_until:
                k.status = KeyStatus.active
                k.failures = 0
                k.cooldown_until = 0.0

    def available(self) -> list[KeyState]:
        self._refresh()
        return [k for k in self._keys if k.status == KeyStatus.active]

    def pick(self) -> KeyState | None:
        """Выбрать следующий здоровый ключ round-robin, либо None."""
        healthy = self.available()
        if not healthy:
            return None
        # Идём по общему списку от курсора, чтобы распределение было равномерным.
        n = len(self._keys)
        for offset in range(n):
            idx = (self._cursor + offset) % n
            k = self._keys[idx]
            if k.status == KeyStatus.active:
                self._cursor = (idx + 1) % n
                return k
        return None

    def report_success(self, key_id: str) -> None:
        for k in self._keys:
            if k.key_id == key_id:
                k.failures = 0
                return

    def report_rate_limit(self, key_id: str) -> None:
        """Пометить ключ как rate-limited и увести в cooldown."""
        for k in self._keys:
            if k.key_id == key_id:
                k.status = KeyStatus.rate_limited
                k.cooldown_until = self._now() + self._cooldown
                return

    def report_failure(self, key_id: str) -> None:
        """Инкремент ошибок; при достижении порога — исчерпание с cooldown."""
        for k in self._keys:
            if k.key_id == key_id:
                k.failures += 1
                if k.failures >= self._failure_threshold:
                    k.status = KeyStatus.exhausted
                    k.cooldown_until = self._now() + self._cooldown
                return
