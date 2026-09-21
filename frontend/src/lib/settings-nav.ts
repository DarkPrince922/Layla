// Settings sections (spec §5 / §3). Each is a route under /settings.
export interface SettingsSection {
  slug: string;
  label: string;
  blurb: string;
}

export const SETTINGS_SECTIONS: SettingsSection[] = [
  { slug: "general", label: "General", blurb: "Hotkeys, projects directory, layout." },
  { slug: "appearance", label: "Appearance", blurb: "Themes and accents." },
  { slug: "providers", label: "Providers", blurb: "LLM connection profiles (via LiteLLM)." },
  { slug: "agent", label: "Agent", blurb: "Ultracode orchestration, budgets, diagnostics." },
  { slug: "personas", label: "Personas", blurb: "AI roles, tool access and boundaries." },
  { slug: "knowledge", label: "Knowledge", blurb: "RAG documents indexed into pgvector." },
  { slug: "security", label: "Security", blurb: "Access policies, confirmations, audit log." },
  { slug: "integrations", label: "Integrations", blurb: "MCP servers, Telegram, intelligence APIs." },
  { slug: "local-api", label: "Local API", blurb: "Layla's own OpenAI-compatible endpoint." },
  { slug: "data", label: "Data", blurb: "Export, import, retention, cleanup." },
  { slug: "accounts-lab", label: "Accounts Lab", blurb: "Provider onboarding, rotation, combos." },
  { slug: "privacy-chain", label: "Privacy Chain", blurb: "Global egress profile (Direct/Tor/Proxy)." },
];
