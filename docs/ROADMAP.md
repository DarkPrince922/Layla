# Roadmap (milestones)

Each milestone is a working application, verified before moving on (spec §9).

- **M0 — Skeleton** ✅ *(this repo)* — monorepo, Docker Compose (LiteLLM +
  Postgres + Redis + Caddy), auth, data model, domain sidebar, Settings shell.
- **M1 — Providers & chat** ✅ *(this repo)* — Providers + Accounts Lab over
  LiteLLM, key rotation with circuit breaker, streaming chat with personas,
  Code domain with file tree and repo import.
- **M2 — Design + Knowledge + MCP** ✅ *(this repo)* — Design brief → sandboxed
  iframe artifact with Preview/Code + breakpoints, RAG (chunk + embed + cosine
  search), MCP client + registry with connection test, Telegram bot.
- **M3 — OSINT** ✅ — persistent cases, source-attributed artifacts, query
  timeline, encrypted intelligence keys, Shodan/VT/SecurityTrails/urlscan via
  a bundled read-only MCP server. Passive domain/IP lookups and manual material
  collection for all case types. See [OSINT usage and limits](OSINT.md).
- **M4 — Pentest (core)** ✅ — engagements, Scope + authorized gate, execution
  venue + SSH servers + egress route, findings with filters, Acunetix HTML
  import with dedup + SHA-256, markdown report generation. **Scope-enforcement
  and egress fail-closed shipped with tests** (`app/services/scope.py`,
  `egress.py`, `venue_gate.py`).
- **M5 — Autonomous agent** — CAI integrated as an MCP engine, Pentest Agent
  (live log, HITL, finding triage), Ultracode sub-agent orchestration, budgets.
- **M6 — Polish** — Combos router, audit export, mobile layout, RU i18n,
  hardening, docs.

## Gaps found while taking over from M2

The M0–M2 labels above describe shipped code, not full spec acceptance.
The following remain tracked explicitly:

- Knowledge uses JSON-stored hash embeddings and Python cosine search, not
  pgvector queries; retrieval is not wired into every chat turn.
- Telegram supports encrypted configuration and a test send, not inbound chat
  control or subscriptions to running-agent updates.
- A post-M2 fix routed chat/design directly to providers. Restore the mandated
  LiteLLM-only path with working proxy model synchronization and fallback tests.
- General tool-enabled chat, per-persona execution enforcement and OSINT
  specialist-agent coordination belong to the agent milestone, not this M3 UI.
- M3 fixes the missing MCP dependency, adds actual tool calls and Streamable
  HTTP support, and encrypts legacy MCP environment secrets during migration.
- M3 makes OSINT and integrations usable on narrow screens. The explicit
  desktop/mobile preference and complete EN/RU dictionaries remain in M6.
