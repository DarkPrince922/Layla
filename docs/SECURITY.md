# Security model

Layla is a tool for **authorized security testing**. The controls below are
mandatory product behavior (spec §7). This document describes how each is
represented in the M0 skeleton and where enforcement lands.

## Authorized-use posture

Pentest capabilities operate only inside an engagement the operator has marked
authorized, against a confirmed scope. Layla does not provide, and will not
provide, features whose purpose is to bypass authorization or scope, to act
against systems the operator is not authorized to test, or to conceal activity
on third-party systems.

## Controls and where they live

| Control (spec §7) | M0 representation | Enforced in |
|---|---|---|
| Authorized-workspace gate | `Engagement.authorized` (bool, default false); `Scope.confirmed` | M4 service layer + UI gate |
| Hard scope enforcement | `Scope.allow[]`, `Scope.deny[]` | M4 target-check before every active action / attack-box command (with unit tests) |
| Venue isolation | `Venue.mode` ∈ {analysis_only, attack_box, this_machine}; default analysis_only | M4/M5 command dispatch |
| Egress fail-closed | `Venue.egress_route`, `Server.egress_route` ∈ {inherit, direct, tor, proxy} | M4 SSH/egress routing (fail-closed, no Direct fallback) |
| Encrypted secrets | `secret_ref` columns; `app.security.crypto` (Fernet); UI masking | M0 (done) |
| Plain-HTTP key block | `HttpKeyBanner`; key inputs disabled until acknowledged | M0 (done) |
| Audit log | `AuditLog` table; `app.services.audit.record()` | M0 (auth/provider events); expanded per milestone |
| HITL confirmations | `Persona.hitl_required` (default true for Security/Pentest/OSINT) | M5 agent pause points |

## Secrets at rest

- API keys (providers, intelligence APIs) and SSH private keys are encrypted with
  a Fernet key derived from `LAYLA_SECRET_KEY`. In production, supply a real
  generated Fernet key; the app derives a stable dev key otherwise so local runs
  work, which is **not** safe for production.
- Plaintext secrets never appear in API responses. Endpoints expose only a
  `has_secret` flag (and, where useful, a masked form). This is covered by a test
  (`tests/test_auth_flow.py::test_provider_api_key_never_returned`).

## Transport

- Production requires HTTPS; Caddy provisions certificates automatically when
  `LAYLA_PUBLIC_HOST` is a real domain.
- Session cookies are `httpOnly`, `SameSite=Lax`, and `Secure` in production.

## M5 — автономный агент (границы безопасности)

Автономный/интерактивный Pentest-агент управляем и не обходит гейты §7:

- **Гейт на каждое активное действие.** Любая команда агента к цели проходит
  `venue_executor.execute` → `venue_gate` (активная площадка + `authorized` +
  подтверждённый scope + цель в allow-list) и `egress` (Tor/Proxy fail-closed).
  В `analysis_only` активные действия запрещены полностью.
- **HITL.** В `interactive`-режиме каждая команда ждёт подтверждения оператора;
  в `autonomous` — как минимум опасные команды и всё, что требует персона
  (`hitl_required`). Классификатор опасных команд — `venue_executor.is_dangerous`.
- **Исполнитель не подключён по умолчанию.** Даже пройдя все гейты, команда не
  выполняется, пока оператор осознанно не подключит исполнитель на attack box;
  иначе шаг помечается «исполнитель не подключён».
- **CAI** интегрируется только как движок за этими гейтами (`services/cai_engine.py`),
  опционально; сам по себе ничего не запускает.
- **Триаж** находок — только анализ моделью, без активных действий.
- **Бюджеты** (токены/стоимость/время) ограничивают работу агента.

Всё это покрыто тестами (`tests/test_agent_core.py`, `tests/test_agent_api.py`):
блокировка вне scope, без авторизации, в analysis_only, при сбое Tor, а также
паузы HITL.
