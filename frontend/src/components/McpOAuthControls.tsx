"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function McpOAuthControls({ serverId }: { serverId: string }) {
  const qc = useQueryClient();
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [scope, setScope] = useState("");
  const state = useQuery({ queryKey: ["code-mcp-oauth"], queryFn: () => api.get<Record<string, string>>("/code/mcp/oauth") });
  const auth = useMutation({ mutationFn: async () => {
    const result = await api.post<{ authorization_url: string }>(`/code/mcp/${serverId}/oauth`, {
      client_id: clientId || null, client_secret: secret || null, scope: scope || null,
    });
    setSecret("");
    window.location.assign(result.authorization_url);
  }});
  const disconnect = useMutation({ mutationFn: () => api.del(`/code/mcp/${serverId}/oauth`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["code-mcp-oauth"] }) });
  const status = state.data?.[serverId];
  return <div className="mt-3 space-y-2 border-t border-ink-700 pt-3 text-xs">
    <div className="flex items-center gap-3"><span className="text-neutral-400">OAuth для Code · {status === "connected" ? "подключён" : status === "pending" ? "ожидает входа" : "не подключён"}</span>
      <button disabled={auth.isPending} onClick={() => auth.mutate()} className="text-accent-300">{auth.isPending ? "Подключение…" : "Войти через OAuth"}</button>
      {status && <button onClick={() => disconnect.mutate()} disabled={disconnect.isPending} className="text-neutral-400">Отключить OAuth</button>}
    </div>
    <details><summary className="cursor-pointer text-neutral-500">Параметры OAuth-клиента</summary>
      <p className="mt-2 text-neutral-500">Оставьте пустыми для автоматической регистрации. Для заранее зарегистрированного клиента укажите его параметры.</p>
      <input value={clientId} onChange={e => setClientId(e.target.value)} placeholder="Client ID" aria-label="OAuth Client ID" autoComplete="off" className="mt-2 w-full rounded bg-ink-900 p-2" />
      <input type="password" value={secret} onChange={e => setSecret(e.target.value)} placeholder="Client secret (если требуется)" aria-label="OAuth client secret" autoComplete="new-password" className="mt-2 w-full rounded bg-ink-900 p-2" />
      <input value={scope} onChange={e => setScope(e.target.value)} placeholder="Scope (по умолчанию — требования MCP-сервера)" aria-label="OAuth scope" className="mt-2 w-full rounded bg-ink-900 p-2" />
    </details>
    {(auth.error || disconnect.error || state.error) && <p role="alert" className="text-red-300">{auth.error?.message || disconnect.error?.message || state.error?.message}</p>}
  </div>;
}
