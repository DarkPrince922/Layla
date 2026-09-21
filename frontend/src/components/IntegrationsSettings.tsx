"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, Trash2, Plus, Send, CheckCircle2, XCircle } from "lucide-react";
import {
  api,
  type McpServer,
  type McpTestResult,
  type TelegramConfig,
} from "@/lib/api";
import { HttpKeyBanner } from "@/components/HttpKeyBanner";

function McpRegistry() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", transport: "stdio", command: "", url: "" });
  const [testResults, setTestResults] = useState<Record<string, McpTestResult>>({});

  const { data: servers = [] } = useQuery({
    queryKey: ["mcp"],
    queryFn: () => api.get<McpServer[]>("/mcp/servers"),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post("/mcp/servers", {
        name: form.name,
        transport: form.transport,
        command: form.transport === "stdio" ? form.command : null,
        url: form.transport === "http" ? form.url : null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcp"] });
      setOpen(false);
      setForm({ name: "", transport: "stdio", command: "", url: "" });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/mcp/servers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mcp"] }),
  });
  const toggle = useMutation({
    mutationFn: (v: { id: string; enabled: boolean }) =>
      api.patch(`/mcp/servers/${v.id}`, { enabled: v.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mcp"] }),
  });
  const test = useMutation({
    mutationFn: (id: string) => api.post<McpTestResult>(`/mcp/servers/${id}/test`),
    onSuccess: (res, id) => setTestResults((prev) => ({ ...prev, [id]: res })),
  });

  return (
    <div className="mb-8">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-300">MCP-серверы</h2>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1 rounded-md bg-indigo-600 px-2.5 py-1 text-xs text-white"
        >
          <Plus className="h-3.5 w-3.5" /> Добавить
        </button>
      </div>

      {open && (
        <div className="mb-3 space-y-2 rounded-lg border border-ink-700 bg-ink-900 p-3">
          <div className="flex gap-2">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Название"
              className="flex-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
            />
            <select
              value={form.transport}
              onChange={(e) => setForm({ ...form, transport: e.target.value })}
              className="rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
            >
              <option value="stdio">stdio</option>
              <option value="http">http</option>
            </select>
          </div>
          {form.transport === "stdio" ? (
            <input
              value={form.command}
              onChange={(e) => setForm({ ...form, command: e.target.value })}
              placeholder="Команда, напр. npx -y @modelcontextprotocol/server-filesystem /path"
              className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
            />
          ) : (
            <input
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              placeholder="URL сервера, напр. http://localhost:8080/sse"
              className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
            />
          )}
          <button
            onClick={() => create.mutate()}
            disabled={!form.name || create.isPending}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs text-white disabled:opacity-50"
          >
            Зарегистрировать
          </button>
        </div>
      )}

      {servers.length === 0 ? (
        <p className="rounded-lg border border-dashed border-ink-700 p-4 text-center text-xs text-neutral-500">
          Нет серверов. Добавьте MCP-сервер (stdio или http).
        </p>
      ) : (
        <ul className="space-y-2">
          {servers.map((s) => {
            const res = testResults[s.id];
            return (
              <li key={s.id} className="rounded-lg border border-ink-700 bg-ink-900 p-3">
                <div className="flex items-center gap-2">
                  <Plug className="h-4 w-4 text-neutral-500" />
                  <span className="text-sm font-medium">{s.name}</span>
                  <span className="rounded bg-ink-700 px-1.5 py-0.5 text-[10px] text-neutral-400">
                    {s.transport}
                  </span>
                  <label className="ml-auto flex items-center gap-1 text-[11px] text-neutral-400">
                    <input
                      type="checkbox"
                      checked={s.enabled}
                      onChange={(e) => toggle.mutate({ id: s.id, enabled: e.target.checked })}
                    />
                    включён
                  </label>
                  <button
                    onClick={() => test.mutate(s.id)}
                    className="rounded bg-ink-700 px-2 py-1 text-[11px] text-neutral-200 hover:bg-ink-600"
                  >
                    Проверить
                  </button>
                  <button
                    onClick={() => remove.mutate(s.id)}
                    className="text-neutral-500 hover:text-red-400"
                    aria-label="Удалить"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
                <div className="mt-1 truncate text-[11px] text-neutral-500">
                  {s.command || s.url}
                </div>
                {res && (
                  <div className="mt-2 text-xs">
                    {res.ok ? (
                      <div className="flex items-start gap-1 text-emerald-300">
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5" />
                        <span>Инструменты: {res.tools.map((t) => t.name).join(", ") || "—"}</span>
                      </div>
                    ) : (
                      <div className="flex items-start gap-1 text-amber-300">
                        <XCircle className="mt-0.5 h-3.5 w-3.5" />
                        <span>{res.error}</span>
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function TelegramIntegration() {
  const qc = useQueryClient();
  const [token, setToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [insecure, setInsecure] = useState(false);
  const [ack, setAck] = useState(false);
  const [testMsg, setTestMsg] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined" && !window.isSecureContext) setInsecure(true);
  }, []);

  const { data: cfg } = useQuery({
    queryKey: ["telegram"],
    queryFn: () => api.get<TelegramConfig>("/integrations/telegram"),
  });

  useEffect(() => {
    if (cfg?.default_chat_id) setChatId(cfg.default_chat_id);
  }, [cfg]);

  const save = useMutation({
    mutationFn: () =>
      api.put("/integrations/telegram", {
        bot_token: token,
        default_chat_id: chatId,
        enabled: true,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["telegram"] });
      setToken("");
    },
  });
  const test = useMutation({
    mutationFn: () => api.post<{ ok: boolean }>("/integrations/telegram/test", {}),
    onSuccess: () => setTestMsg("Отправлено ✅"),
    onError: (e) => setTestMsg(e instanceof Error ? e.message : "Ошибка"),
  });

  const keyBlocked = insecure && !ack;

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-neutral-300">Telegram-бот</h2>
      <p className="mb-2 text-xs text-neutral-500">
        Управляйте чатами с телефона и получайте статусы агента. Токен хранится
        зашифрованным.
      </p>
      {cfg?.configured && (
        <p className="mb-2 text-xs text-emerald-300">
          Настроен: {cfg.token_masked} · chat_id {cfg.default_chat_id || "—"}
        </p>
      )}
      <div className="space-y-2 rounded-lg border border-ink-700 bg-ink-900 p-3">
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
          type="password"
          disabled={keyBlocked}
          placeholder={keyBlocked ? "Ввод заблокирован по HTTP" : "Токен бота (от @BotFather)"}
          className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs disabled:opacity-40"
        />
        <input
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
          placeholder="chat_id по умолчанию"
          className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
        />
        {insecure && (
          <label className="flex items-center gap-2 text-[11px] text-amber-300">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            Понимаю риск ввода токена по обычному HTTP.
          </label>
        )}
        <div className="flex items-center gap-2">
          <button
            onClick={() => save.mutate()}
            disabled={!token || save.isPending}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs text-white disabled:opacity-50"
          >
            {save.isPending ? "Сохранение…" : "Сохранить"}
          </button>
          {cfg?.configured && (
            <button
              onClick={() => test.mutate()}
              disabled={test.isPending}
              className="flex items-center gap-1 rounded-md bg-ink-700 px-3 py-1.5 text-xs text-white"
            >
              <Send className="h-3.5 w-3.5" /> Тест
            </button>
          )}
          {testMsg && <span className="text-xs text-neutral-400">{testMsg}</span>}
        </div>
      </div>
    </div>
  );
}

export function IntegrationsSettings() {
  return (
    <div>
      <h1 className="text-xl font-semibold">Интеграции</h1>
      <p className="mb-4 text-sm text-neutral-500">
        MCP-серверы, Telegram-бот. Intelligence-API (Shodan/VirusTotal/…) появятся
        в M3.
      </p>
      <div className="mb-4">
        <HttpKeyBanner />
      </div>
      <McpRegistry />
      <TelegramIntegration />
    </div>
  );
}
