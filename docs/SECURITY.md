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
