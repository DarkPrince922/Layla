"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, KeyRound, Trash2 } from "lucide-react";
import { api, type IntelProvider, type McpTestResult } from "@/lib/api";

function ProviderKey({ provider, blocked }: { provider: IntelProvider; blocked: boolean }) {
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: () => api.put(`/integrations/intelligence/${provider.provider}`, { api_key: key }),
    onSuccess: () => {
      setKey(""); setSaved(true);
      qc.invalidateQueries({ queryKey: ["intelligence"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/integrations/intelligence/${provider.provider}`),
    onSuccess: () => {
      setSaved(false);
      qc.invalidateQueries({ queryKey: ["intelligence"] });
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!blocked && key.trim()) save.mutate();
  }
  const pending = save.isPending || remove.isPending;
  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-ink-700 bg-ink-900 p-4">
      <div className="flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-cyan-400" />
        <h3 className="text-sm font-medium">{provider.name}</h3>
        <a href={provider.docs_url} target="_blank" rel="noopener noreferrer"
          className="ml-auto flex items-center gap-1 text-xs text-neutral-400 hover:text-white">
          API docs <ExternalLink className="h-3 w-3" />
        </a>
      </div>
      <p className="text-xs text-neutral-400">
        {provider.supports_ip ? "Домены и публичные IP" : "Домены"} · пассивный поиск
      </p>
      <p className="text-xs text-neutral-500">
        {provider.configured ? `Ключ сохранён: ${provider.key_masked}` :
          provider.key_required ? "Для запросов нужен API-ключ" : "Доступен без ключа с меньшим лимитом"}
      </p>
      <label className="block text-xs text-neutral-400">
        API-ключ {provider.name}
        <input type="password" autoComplete="new-password" value={key} disabled={blocked || pending}
          onChange={(e) => { setKey(e.target.value); setSaved(false); }}
          placeholder={blocked ? "Подтвердите ввод по HTTP" : "Вставьте новый ключ"}
          className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm disabled:opacity-40" />
      </label>
      <div className="flex items-center gap-3">
        <button type="submit" disabled={blocked || !key.trim() || pending}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs text-white disabled:opacity-40">
          {save.isPending ? "Сохранение…" : provider.configured ? "Заменить ключ" : "Сохранить ключ"}
        </button>
        {provider.configured && (
          <button type="button" onClick={() => remove.mutate()} disabled={pending}
            aria-label={`Удалить ключ ${provider.name}`}
            className="flex items-center gap-1 text-xs text-neutral-400 hover:text-red-400 disabled:opacity-40">
            <Trash2 className="h-3.5 w-3.5" /> Удалить
          </button>
        )}
        {saved && <span role="status" className="text-xs text-emerald-300">Сохранён</span>}
      </div>
      {(save.error || remove.error) && <p role="alert" className="text-xs text-red-300">
        {(save.error || remove.error)?.message}
      </p>}
    </form>
  );
}

export function IntelligenceSettings() {
  // Block until the browser has established the actual transport; localhost
  // over HTTP still needs explicit acknowledgement, just like other HTTP hosts.
  const [https, setHttps] = useState(false);
  const [ready, setReady] = useState(false);
  const [ack, setAck] = useState(false);
  useEffect(() => { setHttps(window.location.protocol === "https:"); setReady(true); }, []);
  const providers = useQuery({
    queryKey: ["intelligence"],
    queryFn: () => api.get<IntelProvider[]>("/integrations/intelligence"),
  });
  const test = useMutation({
    mutationFn: () => api.post<McpTestResult>("/integrations/intelligence/test", {}),
  });
  return (
    <section className="mb-8 space-y-3" aria-labelledby="intelligence-title">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="intelligence-title" className="text-sm font-semibold text-neutral-300">Intelligence APIs</h2>
        <button onClick={() => test.mutate()} disabled={test.isPending}
          className="ml-auto rounded-md bg-ink-700 px-3 py-1.5 text-xs disabled:opacity-40">
          {test.isPending ? "Подключение…" : "Проверить встроенный MCP"}
        </button>
      </div>
      <p className="text-xs leading-relaxed text-neutral-500">
        Источники для OSINT-кейсов. Встроенный MCP ищет существующие записи в базах провайдеров.
        Ключи хранятся зашифрованными. Проверка MCP не расходует квоту источников.
      </p>
      {ready && !https && (
        <label className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          Соединение HTTP: ключ передаётся открытым текстом. Понимаю риск и разрешаю ввод.
        </label>
      )}
      {providers.isPending && <p className="text-sm text-neutral-500">Загрузка источников…</p>}
      {providers.error && <p role="alert" className="text-sm text-red-300">{providers.error.message}</p>}
      {test.error && <p role="alert" className="text-xs text-red-300">{test.error.message}</p>}
      {test.data && <p role="status" className={`text-xs ${test.data.ok ? "text-emerald-300" : "text-red-300"}`}>
        {test.data.ok ? `MCP готов. Инструментов: ${test.data.tools.length}.` : test.data.error}
      </p>}
      <div className="grid gap-3 xl:grid-cols-2">
        {providers.data?.map((p) => <ProviderKey key={p.provider} provider={p} blocked={!ready || (!https && !ack)} />)}
      </div>
    </section>
  );
}
