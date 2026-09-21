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
      detail = body.detail || detail;
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

export interface Me {
  id: string;
  email: string;
  display_name?: string | null;
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
  color?: string | null;
  instructions?: string | null;
  is_builtin: boolean;
  hitl_required: boolean;
}

export interface ModelInfo {
  name: string;
  provider: string;
  provider_id: string;
}

export interface Chat {
  id: string;
  domain: string;
  title?: string | null;
  persona_id?: string | null;
  model?: string | null;
}

export interface ChatMessage {
  id: string;
  role: string;
  content: string;
  reasoning?: string;
}

export interface ChatDetail extends Chat {
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

interface StreamHandlers {
  onDelta: (text: string) => void;
  onReasoning?: (text: string) => void;
  onDone?: (messageId: string) => void;
  onError?: (message: string) => void;
}

// Стриминг ответа ассистента по SSE. Парсит события `data: {...}`.
export async function streamChat(
  chatId: string,
  content: string,
  model: string | undefined,
  handlers: StreamHandlers,
): Promise<void> {
  const res = await fetch(`${BASE}/chats/${chatId}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, model }),
  });
  if (!res.ok || !res.body) {
    handlers.onError?.(`Ошибка ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 2);
      if (!line.startsWith("data:")) continue;
      const data = line.slice(5).trim();
      try {
        const evt = JSON.parse(data);
        if (evt.delta) handlers.onDelta(evt.delta);
        else if (evt.reasoning) handlers.onReasoning?.(evt.reasoning);
        else if (evt.error) handlers.onError?.(evt.error);
        else if (evt.done) handlers.onDone?.(evt.message_id);
      } catch {
        /* игнорируем неполные чанки */
      }
    }
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
