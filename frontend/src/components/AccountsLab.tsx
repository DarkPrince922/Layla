"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, KeyRound, RefreshCw } from "lucide-react";
import {
  api,
  type AccountsHealth,
  type ProviderKey,
} from "@/lib/api";
import { HttpKeyBanner } from "@/components/HttpKeyBanner";
import { CombosManager } from "@/components/CombosManager";

interface Provider {
  id: string;
  name: string;
  kind: string;
  active: boolean;
}

function HealthTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900 p-4">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-xs text-neutral-500">{label}</div>
    </div>
  );
}

function KeyManager({ provider }: { provider: Provider }) {
  const qc = useQueryClient();
  const [insecure, setInsecure] = useState(false);
  const [ack, setAck] = useState(false);
  const [newKey, setNewKey] = useState("");

  useEffect(() => {
    if (typeof window !== "undefined" && !window.isSecureContext) setInsecure(true);
  }, []);

  const { data: keys = [] } = useQuery({
    queryKey: ["keys", provider.id],
    queryFn: () => api.get<ProviderKey[]>(`/providers/${provider.id}/keys`),
  });

  const add = useMutation({
    mutationFn: () => api.post(`/providers/${provider.id}/keys`, { api_key: newKey }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["keys", provider.id] });
      qc.invalidateQueries({ queryKey: ["accounts-health"] });
      setNewKey("");
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/providers/keys/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["keys", provider.id] });
      qc.invalidateQueries({ queryKey: ["accounts-health"] });
    },
  });
  const setStatus = useMutation({
    mutationFn: (v: { id: string; status: string }) =>
      api.post(`/providers/keys/${v.id}/status`, { status: v.status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["keys", provider.id] });
      qc.invalidateQueries({ queryKey: ["accounts-health"] });
    },
  });

  const keyBlocked = insecure && !ack;

  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900 p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">{provider.name}</span>
        <span className="rounded bg-ink-700 px-1.5 py-0.5 text-[10px] text-neutral-400">
          {provider.kind}
        </span>
        <span className="ml-auto text-[11px] text-neutral-500">
          Ключей для ротации: {keys.length}
        </span>
      </div>

      <ul className="mb-3 divide-y divide-ink-800">
        {keys.map((k) => (
          <li key={k.id} className="flex items-center gap-2 py-1.5 text-xs">
            <KeyRound className="h-3.5 w-3.5 text-neutral-500" />
            <span className="font-mono">{k.masked}</span>
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] ${
                k.status === "active"
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "bg-amber-500/20 text-amber-300"
              }`}
            >
              {k.status}
            </span>
            <div className="ml-auto flex items-center gap-2">
              {k.status !== "active" ? (
                <button
                  onClick={() => setStatus.mutate({ id: k.id, status: "active" })}
                  className="flex items-center gap-1 text-neutral-500 hover:text-emerald-300"
                >
                  <RefreshCw className="h-3 w-3" /> вернуть
                </button>
              ) : (
                <button
                  onClick={() => setStatus.mutate({ id: k.id, status: "disabled" })}
                  className="text-neutral-500 hover:text-amber-300"
                >
                  отключить
                </button>
              )}
              <button
                onClick={() => remove.mutate(k.id)}
                className="text-neutral-500 hover:text-red-400"
                aria-label="Удалить ключ"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          </li>
        ))}
        {keys.length === 0 && (
          <li className="py-1.5 text-xs text-neutral-600">
            Нет ключей. Добавьте несколько для автоматической ротации при лимитах.
          </li>
        )}
      </ul>

      <div className="flex items-center gap-2">
        <input
          type="password"
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          disabled={keyBlocked}
          placeholder={keyBlocked ? "Ввод заблокирован по HTTP" : "Новый API-ключ (шифруется)"}
          className="flex-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs disabled:opacity-40"
        />
        <button
          onClick={() => add.mutate()}
          disabled={!newKey || add.isPending}
          className="flex items-center gap-1 rounded-md bg-indigo-600 px-2 py-1.5 text-xs text-white disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" /> Добавить ключ
        </button>
      </div>
      {insecure && (
        <label className="mt-2 flex items-center gap-2 text-[11px] text-amber-300">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          Понимаю риск ввода ключа по обычному HTTP.
        </label>
      )}
    </div>
  );
}

export function AccountsLab() {
  const { data: health } = useQuery({
    queryKey: ["accounts-health"],
    queryFn: () => api.get<AccountsHealth>("/providers/accounts/health"),
  });
  const { data: providers = [] } = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.get<Provider[]>("/providers"),
  });

  return (
    <div>
      <h1 className="text-xl font-semibold">Accounts Lab</h1>
      <p className="mb-4 text-sm text-neutral-500">
        Каталог подключения провайдеров и ротация ключей. Несколько ключей одного
        провайдера автоматически ротируются при rate limit / исчерпании квоты.
      </p>

      <div className="mb-4">
        <HttpKeyBanner />
      </div>

      <div className="mb-6 grid grid-cols-4 gap-3">
        <HealthTile label="Профили" value={health?.profiles ?? 0} />
        <HealthTile label="Активные модели" value={health?.active_models ?? 0} />
        <HealthTile label="OAuth-аккаунты" value={health?.oauth_accounts ?? 0} />
        <HealthTile label="Лимит квоты" value={health?.quota_limited ?? 0} />
      </div>

      <h2 className="mb-2 text-sm font-semibold text-neutral-300">Провайдеры с ключами</h2>
      {providers.length === 0 ? (
        <p className="rounded-lg border border-dashed border-ink-700 p-6 text-center text-sm text-neutral-500">
          Сначала создайте профиль в разделе «Провайдеры».
        </p>
      ) : (
        <div className="space-y-3">
          {providers.map((p) => (
            <KeyManager key={p.id} provider={p} />
          ))}
        </div>
      )}

      <div className="mt-6 border-t border-ink-700 pt-5"><CombosManager /></div>
    </div>
  );
}
