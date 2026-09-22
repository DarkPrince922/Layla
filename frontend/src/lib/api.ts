// Thin fetch wrapper. All calls are same-origin under /api (Caddy in prod,
// Next.js rewrites in dev), so session cookies flow automatically.
const BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) {
        detail = body.detail.map((e: { msg?: string }) => e.msg || "Некорректные данные").join("; ");
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(p: string) => request<T>(p, { method: "DELETE" }),
};

// ---- OSINT / intelligence (M3) ----
export type IntelProviderName = "shodan" | "virustotal" | "securitytrails" | "urlscan";
export interface IntelProvider {
  provider: IntelProviderName;
  name: string;
  docs_url: string;
  tool_name: string;
  supports_ip: boolean;
  key_required: boolean;
  configured: boolean;
  key_masked: string | null;
  passive: boolean;
}
export type SubjectType = "person" | "company" | "domain";
export interface OsintCase {
  id: string;
  subject_type: SubjectType;
  subject: string;
  artifact_count: number;
  lookup_count: number;
  created_at: string;
}
export interface OsintArtifact {
  id: string;
  provider: string;
  target: string;
  kind: string;
  title: string;
  summary: string;
  source_url: string;
  data: Record<string, unknown>;
  created_at: string;
}
export interface OsintLookup {
  id: string;
  provider: IntelProviderName;
  target: string;
  status: "ok" | "empty" | "error";
  artifact_count: number;
  duplicate_count: number;
  error_code: string | null;
  error: string | null;
  created_at: string;
}
export interface OsintSource {
  provider: string;
  source_url: string;
  artifact_count: number;
}

export interface JobStep {
  text: string;
  at?: string | null;
}

export interface Job {
  chat_id?: string | null;
  created_at?: string;
  updated_at?: string;
  id: string;
  domain: string;
  kind: string;
  title: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number;
  reasoning: string;
  steps: JobStep[];
  result: Record<string, unknown>;
  error?: string | null;
}

export interface Me {
  id: string;
  email: string;
  display_name?: string | null;
  is_admin?: boolean;
  is_active?: boolean;
  must_change_password?: boolean;
}

// ---- Управление пользователями (админ) ----
export interface AdminUser {
  id: string;
  email: string;
  display_name?: string | null;
  is_admin: boolean;
  is_active: boolean;
  must_change_password: boolean;
  created_at?: string | null;
}

export interface AdminUserCreated {
  user: AdminUser;
  generated_password?: string | null;
}

export interface Bootstrap {
  available: boolean;
  email?: string | null;
  password?: string | null;
}

export interface AppMeta {
  env: string;
  is_prod: boolean;
  domains: string[];
  https_required: boolean;
}

// ---- Типы M1 ----
export interface Persona {
  id: string;
  name: string;
  kind: string;
  icon?: string | null;
  color?: string | null;
  instructions?: string | null;
  is_builtin: boolean;
  hitl_required: boolean;
  allowed_tools?: string[];
  /** Что подставить в чат при выборе роли. */
  default_model?: string | null;
  default_mode?: "auto" | "confirm" | "plan" | null;
}

export interface ModelInfo {
  name: string;
  provider: string;
  provider_id: string;
}

export interface Chat {
  project_id?: string | null;
  id: string;
  domain: string;
  title?: string | null;
  persona_id?: string | null;
  model?: string | null;
  provider_id?: string | null;
}

export interface ChatMessage {
  id: string;
  role: string;
  content: string;
  reasoning?: string;
  tools?: ToolEvent[];
  error?: string | null;
  meta?: { reasoning?: string; tools?: ToolEvent[]; error?: string | null; mode?: "auto" | "confirm" | "plan" };
}

export interface ChatDetail extends Chat {
  last_job?: Job | null;
  messages: ChatMessage[];
}

export interface ProviderKey {
  id: string;
  label?: string | null;
  status: string;
  masked: string;
}

export interface AccountsHealth {
  profiles: number;
  active_models: number;
  oauth_accounts: number;
  quota_limited: number;
}

export interface Project {
  id: string;
  name: string;
  repo_url?: string | null;
  path?: string | null;
}

export interface FileNode {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface FileContent {
  path: string;
  content: string;
  sha256: string;
}

export interface FileChange {
  path: string;
  operation: "create" | "edit" | "delete";
  diff: string;
  before_sha256: string | null;
  after_sha256: string | null;
}

export interface ToolEvent {
  id: string;
  name: string;
  path: string;
  status: "running" | "pending" | "done" | "error" | "rejected";
  error?: string;
  change?: FileChange;
}

export async function downloadProject(project: Project): Promise<void> {
  const res = await fetch(`${BASE}/projects/${project.id}/archive`, { credentials: "include" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail || "Не удалось скачать проект");
  }
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `${project.name.replace(/[/\\]/g, "-")}.zip`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

interface StreamHandlers {
  onDelta: (text: string) => void;
  onReasoning?: (text: string) => void;
  onDone?: (messageId: string) => void;
  onError?: (message: string) => void;
  onTool?: (tool: ToolEvent) => void;
}

// Стриминг ответа ассистента по SSE. Парсит события `data: {...}`.
export async function streamChat(
  chatId: string,
  content: string,
  model: string | undefined,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/chats/${chatId}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, model }),
    signal,
  });
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : `Ошибка ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finished = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 2);
        if (!frame.startsWith("data:")) continue;
        const evt = JSON.parse(frame.slice(5).trim());
        if (evt.delta) handlers.onDelta(evt.delta);
        else if (evt.tool) handlers.onTool?.(evt.tool);
        else if (evt.reasoning) handlers.onReasoning?.(evt.reasoning);
        else if (evt.error) handlers.onError?.(evt.error);
        else if (evt.done) { finished = true; handlers.onDone?.(evt.message_id); }
      }
    }
    if (!finished) throw new Error("Соединение прервано. Проверьте историю перед продолжением.");
  } finally {
    reader.releaseLock();
  }
}

// ---- Типы M2 ----
export interface DesignFile {
  name: string;
  content: string;
  language?: string;
}

export interface Design {
  id: string;
  stack: string;
  brief: Record<string, unknown>;
  files: DesignFile[];
  design_system_ref?: string | null;
  project_id?: string | null;
}

export interface KnowledgeDoc {
  id: string;
  title: string;
  source?: string | null;
  domain?: string | null;
  chunk_count: number;
}

export interface SearchHit {
  chunk_id: string;
  doc_id: string;
  ordinal: number;
  content: string;
  score: number;
}

export interface McpServer {
  id: string;
  name: string;
  transport: string;
  command?: string | null;
  url?: string | null;
  enabled: boolean;
  personas: string[];
  env_keys: string[];
}

export interface McpTestResult {
  ok: boolean;
  tools: { name: string; description?: string | null }[];
  error?: string | null;
}

export interface TelegramConfig {
  configured: boolean;
  enabled: boolean;
  default_chat_id?: string | null;
  token_masked?: string | null;
}

// ---- Типы M4 (Pentest) ----
export interface PentestScope {
  allow: string[];
  deny: string[];
  confirmed: boolean;
}

export interface PentestVenue {
  mode: string;
  attack_box_id?: string | null;
  egress_route: string;
}

export interface Engagement {
  id: string;
  target: string;
  authorized: boolean;
  status: string;
  scope?: PentestScope | null;
  venue?: PentestVenue | null;
}

export interface Finding {
  id: string;
  severity: string;
  type: string;
  url?: string | null;
  method?: string | null;
  param?: string | null;
  status: string;
  source: string;
  title?: string | null;
  description?: string | null;
}

export interface PentestReport {
  id: string;
  format: string;
  sha256_source?: string | null;
  stats: Record<string, unknown>;
  status: string;
}

export interface PentestServer {
  id: string;
  host: string;
  port: number;
  user: string;
  status: string;
  egress_route: string;
  has_key: boolean;
}

// ---- Типы M5 (Agent) ----
export interface AgentStep {
  id: string;
  ordinal: number;
  role: string;
  kind: string;
  status: string;
  requires_hitl: boolean;
  target?: string | null;
  command?: string | null;
  summary?: string | null;
  output?: string | null;
}

export interface AgentRun {
  id: string;
  engagement_id?: string | null;
  task: string;
  mode: string;
  model?: string | null;
  status: string;
  budget_used: Record<string, unknown>;
  steps: AgentStep[];
}

export interface AgentConfig {
  preset: string;
  role_models: Record<string, string>;
  budgets: Record<string, number>;
  diagnostics: Record<string, unknown>;
  context: Record<string, unknown>;
}

export interface AcunetixImportResult {
  engagement_id: string;
  created: boolean;
  scope_hosts: string[];
  stats: Record<string, number>;
  sha256_source?: string | null;
  status: string;
}

export interface ProviderModel {
  name: string;
  enabled: boolean;
  /** Умеет ли модель работать с файлами (инструментами). */
  tools: boolean;
}
