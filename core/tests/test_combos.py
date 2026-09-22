"""Тесты Combos router (спец. §5.1): lockout, восстановление, API."""
from __future__ import annotations

import pytest

from app.services.combos import ModelRouter


def _clock():
    st = {"t": 0.0}
    return (lambda: st["t"]), (lambda dt: st.__setitem__("t", st["t"] + dt))


def test_picks_first_healthy():
    now, _ = _clock()
    r = ModelRouter(["a", "b", "c"], now=now)
    assert r.pick() == "a"


def test_rate_limit_locks_out_immediately():
    now, _ = _clock()
    r = ModelRouter(["a", "b"], now=now)
    r.report_failure("a", rate_limited=True)
    assert r.pick() == "b"


def test_lockout_recovers_after_cooldown():
    now, adv = _clock()
    r = ModelRouter(["a", "b"], cooldown_seconds=60, now=now)
    r.report_failure("a", rate_limited=True)
    assert r.pick() == "b"
    adv(61)
    assert r.pick() == "a"


def test_failure_threshold():
    now, _ = _clock()
    r = ModelRouter(["a", "b"], failure_threshold=2, now=now)
    r.report_failure("a")
    assert r.pick() == "a"  # ещё не залочен (1 < 2)
    r.report_failure("a")
    assert r.pick() == "b"  # теперь залочен


def test_all_locked_returns_none():
    now, _ = _clock()
    r = ModelRouter(["a"], now=now)
    r.report_failure("a", rate_limited=True)
    assert r.pick() is None


@pytest.mark.asyncio
async def test_combo_api_crud_and_route(client):
    await client.post(
        "/api/auth/register", json={"email": "cmb@example.com", "password": "hunter2hunter2"}
    )
    c = (await client.post("/api/combos", json={"name": "fast", "models": ["m1", "m2"]})).json()
    assert c["models"] == ["m1", "m2"]

    lst = (await client.get("/api/combos")).json()
    assert len(lst) == 1

    routed = (await client.get(f"/api/combos/{c['id']}/route")).json()
    assert routed["model"] == "m1"

    await client.patch(f"/api/combos/{c['id']}", json={"models": ["m3"]})
    routed2 = (await client.get(f"/api/combos/{c['id']}/route")).json()
    assert routed2["model"] == "m3"

    assert (await client.delete(f"/api/combos/{c['id']}")).status_code == 204
