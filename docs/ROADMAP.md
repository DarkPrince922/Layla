# Roadmap (milestones)

Each milestone is a working application, verified before moving on (spec §9).

- **M0 — Skeleton** ✅ *(this repo)* — monorepo, Docker Compose (LiteLLM +
  Postgres + Redis + Caddy), auth, data model, domain sidebar, Settings shell.
- **M1 — Providers & chat** — Providers + Accounts Lab over LiteLLM, key
  rotation, streaming chat, built-in personas in chat, Code domain with file
  tree and repo import.
- **M2 — Design + Knowledge + MCP** — Design brief → sandboxed artifact,
  Preview/Code, RAG (pgvector), MCP client + integration registry, Telegram bot.
- **M3 — OSINT** — cases, intelligence APIs (Shodan/VT/SecurityTrails/urlscan)
  via MCP, passive lookups.
- **M4 — Pentest (core)** — engagements, Scope + authorized gate, Servers /
  execution venue + SSH + egress, findings, Acunetix import, reports.
  **Scope-enforcement and egress fail-closed shipped with tests.**
- **M5 — Autonomous agent** — CAI integrated as an MCP engine, Pentest Agent
  (live log, HITL, finding triage), Ultracode sub-agent orchestration, budgets.
- **M6 — Polish** — Combos router, audit export, mobile layout, RU i18n,
  hardening, docs.
