# Layla

**Layla** is a self-hostable, multi-domain AI workstation. You connect your own
AI providers and work across four domains, each with its own UI and tools:

- **Code** — chat agent with access to project files and repositories.
- **Pentest** — authorized engagements: scope, findings, reports, attack-box
  execution over SSH, autonomous/interactive agent. *(core value; guardrails
  built in — see [Security](#security))*
- **OSINT** — open-source recon cases via intelligence APIs (passive by default).
- **Design** — generate UI artifacts (HTML/React/Vue) from a brief, previewed
  in a sandboxed iframe.

A shared layer serves every domain: a provider aggregator with key rotation,
personas (AI roles), sub-agent orchestration, MCP integrations, a knowledge base
(RAG), settings and auth.

> **Design principle:** don't rebuild what open source already provides. Layla is
> a composition — provider aggregation via **LiteLLM**, the pentest engine via
> **CAI** (later milestone), intelligence via **MCP servers** — plus the domain
> logic and UI that tie them together.

---

## Status — All milestones M0–M6 ✅

The UI ships in **Russian** (переключатель EN/RU запланирован на M6). This
repository currently implements **M0** (skeleton), **M1** (providers & chat)
and **M2** (Design + Knowledge + MCP + Telegram), **M3** (OSINT) and
**M4** (Pentest core: scope/authorized gate, venue+egress, findings, Acunetix import)
**M5** (autonomous Pentest agent with HITL, budgets, gated execution) and
**M6** (Combos router, audit export, EN/RU i18n, mobile layout, hardening).
Remaining M0–M2 spec gaps are tracked explicitly in [the roadmap](docs/ROADMAP.md).

**Included now**
- Monorepo layout: `frontend/` · `core/` · `deploy/`.
- Docker Compose dev stack: frontend, core, LiteLLM, Postgres (+pgvector),
  Redis, Caddy.
- **Backend (`core`)** — FastAPI, async SQLAlchemy 2, Pydantic v2:
  - Local auth (email + password with **Argon2id**), sessions as **JWT in an
    httpOnly cookie**.
  - **Encrypted-secrets layer** (Fernet/AES): provider/intel/SSH keys are stored
    as opaque `secret_ref`s, masked in the UI, never returned in plaintext.
  - Full **data model** for every entity in the spec (§6), including the
    security-critical `Engagement.authorized`, `Scope.allow/deny`, `Venue`,
    `Server` (key-only auth) and a mandatory `AuditLog`.
  - Alembic migrations; built-in personas seeded on first login.
  - REST: `/api/auth/*`, `/api/providers` (+ keys, `/accounts/health`),
    `/api/personas`, `/api/models`, `/api/chats/*`, `/api/projects/*`,
    `/api/health`, `/api/meta`.
  - **Tests** for secret encryption/masking, password hashing, the auth flow,
    and the "API keys never leave the server in plaintext" invariant.
- **Frontend (`frontend`)** — Next.js 14 (App Router), React 18, Tailwind,
  TanStack Query, Zustand:
  - Domain sidebar (Code · Pentest · OSINT · Design) + Working/Workspace/Chats.
  - Settings shell with all sections (General, Appearance, Providers, Agent,
    Personas, Knowledge, Security, Integrations, Local API, Data, Accounts Lab,
    Privacy Chain) and a "Back to app" layout.
  - Functional **Providers** and **Personas** settings wired to the backend.
  - Login/register, client-side auth gate.
  - **Plain-HTTP key-entry banner** that blocks key input until acknowledged
    (spec §4/§7.5).

**Added in M1**
- **Providers → LiteLLM**: profiles translate into a LiteLLM `model_list`;
  multiple keys per profile become rotatable entries (`app/services/litellm.py`).
- **Key rotation + circuit breaker** (`app/services/rotation.py`): round-robin
  over healthy keys, rate-limit/exhaustion cooldown, recovery — unit-tested.
- **Accounts Lab**: health tiles (Profiles / Active models / OAuth / Quota-limited)
  and per-provider key management with status control.
- **Streaming chat** (`/api/chats`): SSE token streaming proxied from LiteLLM,
  persona system prompt applied, history persisted; model picker from active
  profiles.
- **Code domain**: projects, **git repo import** (validated URL, shallow clone),
  a lazy **file tree** and file viewer with hard **path-traversal** protection —
  all tested.

**Added in M2**
- **Design domain**: brief → artifact generated via LiteLLM, rendered in an
  **isolated `sandbox` iframe** (`allow-scripts`, no same-origin), Preview/Code
  toggle and desktop/tablet/mobile breakpoints (`/api/designs`).
- **Knowledge / RAG** (`/api/knowledge`): chunking, offline-deterministic
  embeddings (stable hashing, LiteLLM embeddings optional), cosine search,
  per-domain scoping — tested.
- **MCP registry + client** (`/api/mcp/servers`): CRUD for stdio/http servers,
  per-persona enablement, env values never returned, live "test connection"
  via the official `mcp` SDK (installed by default since M3) — tested.
- **Telegram bot** (`/api/integrations/telegram`): encrypted bot token, status
  with masked token, test send — tested.

**Added in M3**
- **OSINT cases**: domain/IP, person and company cases, searchable list,
  manual source-attributed observations, persisted artifacts and query timeline.
- **Passive intelligence through MCP**: Shodan, VirusTotal, SecurityTrails and
  urlscan.io domain/IP lookups through a bundled stdio MCP server; no scan
  submissions or requests to targets. Deduplicated artifacts retain sources.
- **Intelligence API settings**: encrypted provider keys, masked responses,
  HTTP input acknowledgement and a local MCP connection test.
- Owner isolation, safe error reporting, audit records, response size/deadline
  limits, encrypted MCP env migration and Streamable HTTP client support.
- Narrow-screen navigation for OSINT/settings; backend, protocol and migration
  tests plus GitHub Actions backend/frontend checks.

See [M3 usage, upgrade instructions and limits](docs/OSINT.md).

**Added in M4**
- **Pentest domain**: engagements list, authorized-workspace gate, scope
  editor (allow/deny) with confirm, venue selector (analysis/attack-box/this-machine),
  findings with severity filters, **Acunetix HTML import** (dedup + SHA-256),
  markdown report generation, SSH attack-box servers with egress route test.
- **Security core §7 (with tests)**: hard scope-enforcement
  (`app/services/scope.py`), egress **fail-closed** (`egress.py`) — Tor/Proxy
  failure blocks traffic, never falls back to Direct — and the active-action
  gate (`venue_gate.py`). Changing scope revokes authorization.

**Added in M5**
- **Ultracode orchestrator**: LLM decomposes a task into role-tagged steps
  (explorer/reviewer/implementer); plan parsing and HITL policy are pure & tested.
- **Pentest agent** (tab in the Pentest domain): step log, **HITL** approve/deny,
  interactive/autonomous modes, finding **triage** (safe analysis, no active actions).
- **Gated execution** (`app/services/venue_executor.py`): every active command
  passes scope + venue + egress checks before running; without a configured
  attack-box executor a gated command is reported blocked, never executed —
  all covered by tests.
- **Budgets** (tokens/turn, cost, sub-agent minutes) and **Settings → Agent**
  (preset, role models, budgets, diagnostics). **CAI** wired as an optional
  engine strictly behind the gates.

**Added in M6**
- **Combos router** (`app/services/combos.py`): candidate-model routing with
  per-model lockout + circuit-breaker recovery — tested; managed in Accounts Lab.
- **Audit export**: `/api/audit` list and CSV export of the operator's log.
- **i18n EN/RU**: dictionary + language switch in Settings → General (shell,
  navigation, domains, login localized; screen bodies extend incrementally).
- **Layout toggle** desktop/mobile in General, plus responsive navigation.
- **Hardening**: Caddy security headers, refusal to start prod with default
  secrets (`Settings.validate_for_prod`).

**Code workspace update**
- Local projects from scratch, safe text file create/edit/delete, and ZIP downloads.
- Project-bound chat with OpenAI-compatible / Anthropic file tools, persona access
  checks, persisted diffs and chat history; no shell execution in this flow.
- Editable files, stale-version conflict detection, and panels that fit mobile screens.
- [Usage, API, migration and limits](docs/CODE_PROJECTS.md);
  [proposed UI references](docs/UI_REFERENCES.md).

All six milestones (M0–M6) are implemented. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) and [`docs/SECURITY.md`](docs/SECURITY.md).

---

## Quick start

### With Docker Compose (full stack)

```bash
cp .env.example .env
# Edit .env: set LAYLA_SECRET_KEY (see the command in the file), LAYLA_JWT_SECRET,
# POSTGRES_PASSWORD, LITELLM_MASTER_KEY.
docker compose up --build
```

Then open `http://localhost` (Caddy fronts the frontend and proxies `/api`).
In dev this is plain HTTP, so the key-entry banner appears on secret screens —
expected. For production, set `LAYLA_PUBLIC_HOST` to a real domain and Caddy
provisions HTTPS automatically.

### Local dev (without Docker)

Backend:
```bash
cd core
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL="sqlite+aiosqlite:///./layla.db"   # or a local Postgres URL
export LAYLA_SECRET_KEY="$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Frontend (in another shell):
```bash
cd frontend
npm install
npm run dev            # http://localhost:3000 ; /api is proxied to :8000
```

### Tests

```bash
cd core && . .venv/bin/activate && pytest      # backend
cd frontend && npm run build                   # typecheck + lint + build
```

---

## Repository layout

```
Layla/
├── core/            FastAPI backend (Layla Core)
│   ├── app/
│   │   ├── api/         routers: auth, providers, personas, health
│   │   ├── models/      SQLAlchemy models (spec §6)
│   │   ├── schemas/     Pydantic request/response models
│   │   ├── security/    crypto (Fernet), passwords (argon2), jwt
│   │   ├── services/    auth deps, audit log, persona seeding
│   │   ├── config.py    settings from env
│   │   ├── db.py        async engine / session / Base
│   │   └── main.py      app entrypoint
│   ├── alembic/         migrations
│   └── tests/
├── frontend/        Next.js app (Layla Shell)
│   └── src/{app,components,lib,store,styles}
├── deploy/
│   ├── caddy/Caddyfile
│   └── litellm/config.yaml
├── docs/
├── docker-compose.yml
└── .env.example
```

---

## Security

Layla's pentest features are for **authorized testing only**. The guardrails from
the spec (§7) are part of the product, not an option, and the data model encodes
them from M0 so later milestones enforce them rather than bolt them on:

- **Authorized-workspace gate** — an engagement stays `analysis_only` (no shell,
  no target traffic) unless `authorized=true` and a `Scope` is confirmed. Setting
  the flag is an explicit operator action recorded in the audit log.
- **Hard scope enforcement** — `Scope.allow`/`deny` are the allow-list every
  active action and attack-box command will be checked against before running
  (enforced with tests in M4).
- **Venue isolation** — active commands run only on the selected venue;
  `This machine` is off by default and requires explicit opt-in.
- **Egress fail-closed** — with Tor/Proxy selected, a route failure blocks
  traffic rather than falling back to Direct.
- **Encrypted secrets** — provider/intel/SSH keys stored with AES/Fernet, masked
  in the UI, key entry blocked over plain HTTP.
- **Audit log** — sensitive actions are recorded and exportable.
- **HITL** — dangerous agent steps pause for operator confirmation (per-persona).

Explicitly **out of scope and not implemented**: anything that bypasses
scope/authorization, targets unauthorized systems, or hides activity on systems
you don't own.

See [`docs/SECURITY.md`](docs/SECURITY.md).
