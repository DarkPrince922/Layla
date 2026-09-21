"""Тесты ротации ключей и circuit breaker (спец. §8: тесты на ротацию)."""
from __future__ import annotations

from app.models.enums import KeyStatus
from app.services.rotation import KeyRotator, KeyState


def _clock():
    """Управляемые часы для детерминизма."""
    state = {"t": 0.0}

    def now():
        return state["t"]

    def advance(dt):
        state["t"] += dt

    return now, advance


def test_round_robin_over_healthy_keys():
    now, _ = _clock()
    r = KeyRotator(
        [KeyState("a", "s1"), KeyState("b", "s2"), KeyState("c", "s3")],
        now=now,
    )
    picks = [r.pick().key_id for _ in range(6)]
    assert picks == ["a", "b", "c", "a", "b", "c"]


def test_rate_limited_key_is_skipped_then_recovers():
    now, advance = _clock()
    r = KeyRotator([KeyState("a", "s1"), KeyState("b", "s2")], cooldown_seconds=60, now=now)

    r.report_rate_limit("a")
    # "a" в cooldown — выбирается только "b".
    assert {r.pick().key_id for _ in range(4)} == {"b"}

    # По истечении cooldown "a" возвращается в строй.
    advance(61)
    got = {r.pick().key_id for _ in range(4)}
    assert "a" in got and "b" in got


def test_failure_threshold_exhausts_key():
    now, _ = _clock()
    r = KeyRotator([KeyState("a", "s1"), KeyState("b", "s2")], failure_threshold=3, now=now)
    for _ in range(3):
        r.report_failure("a")
    states = {k.key_id: k.status for k in r._keys}
    assert states["a"] == KeyStatus.exhausted
    assert states["b"] == KeyStatus.active
    # "a" исключён из выбора, пока в cooldown.
    assert {r.pick().key_id for _ in range(3)} == {"b"}


def test_no_available_keys_returns_none():
    now, _ = _clock()
    r = KeyRotator([KeyState("a", "s1")], now=now)
    r.report_rate_limit("a")
    assert r.pick() is None


def test_success_resets_failure_counter():
    now, _ = _clock()
    r = KeyRotator([KeyState("a", "s1")], failure_threshold=3, now=now)
    r.report_failure("a")
    r.report_failure("a")
    r.report_success("a")
    r.report_failure("a")
    # 3 отказа не подряд (был success) → ключ ещё активен.
    assert r._keys[0].status == KeyStatus.active
