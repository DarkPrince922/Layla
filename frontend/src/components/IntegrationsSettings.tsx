"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, Trash2, Plus, CheckCircle2, XCircle } from "lucide-react";
import {
  api,
  type McpServer,
  type McpTestResult,
} from "@/lib/api";
import { HttpKeyBanner } from "@/components/HttpKeyBanner";
import { IntelligenceSettings } from "@/components/IntelligenceSettings";
import { TelegramIntegration } from "@/components/TelegramIntegration";

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
          className="flex items-center gap-1 rounded-md bg-accent-600 px-2.5 py-1 text-xs text-white"
        >
          <Plus className="h-3.5 w-3.5" /> Добавить
        </button>
      </div>

      {open && (
        <div className="mb-3 space-y-2 rounded-xl border border-ink-700/70 bg-ink-800/30 p-5">
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
            className="rounded-md bg-accent-600 px-3 py-1.5 text-xs text-white disabled:opacity-50"
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
              <li key={s.id} className="rounded-xl border border-ink-700/70 bg-ink-800/30 p-5">
                <div className="flex items-center gap-2">
                  <Plug className="h-4 w-4 text-neutral-500" />
                  <span className="text-sm font-medium">{s.name}</span>
                  <span className="rounded bg-ink-700 px-1.5 py-0.5 text-[11px] text-neutral-400">
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

export function IntegrationsSettings() {
  return (
    <div>
      <h1 className="text-xl font-semibold">Интеграции</h1>
      <p className="mb-4 text-sm text-neutral-500">
        Intelligence APIs, MCP-серверы и Telegram-бот.
      </p>
      <div className="mb-4">
        <HttpKeyBanner />
      </div>
      <IntelligenceSettings />
      <McpRegistry />
      <TelegramIntegration />
    </div>
  );
}
