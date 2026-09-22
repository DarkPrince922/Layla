"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, Globe2, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import {
  api, type IntelProvider, type IntelProviderName, type OsintArtifact,
  type OsintCase, type OsintLookup, type OsintSource, type SubjectType,
} from "@/lib/api";
import { ChatPanel } from "@/components/ChatPanel";

const inputStyle = "w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm disabled:opacity-40";
const buttonStyle = "rounded-md bg-accent-600 px-3 py-2 text-xs text-white disabled:opacity-40";
const subjectLabels: Record<SubjectType, string> = { domain: "Домен / IP", person: "Человек", company: "Компания" };
const providerLabels: Record<string, string> = {
  shodan: "Shodan", virustotal: "VirusTotal", securitytrails: "SecurityTrails", urlscan: "urlscan.io", manual: "Вручную",
};
const pageSize = 30;

function date(value: string) {
  return new Date(/(Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`).toLocaleString("ru-RU");
}

function ErrorNotice({ error }: { error: Error | null }) {
  return error ? <p role="alert" className="my-2 text-sm text-red-300">{error.message}</p> : null;
}

function SourceLink({ url, children }: { url: string; children?: React.ReactNode }) {
  // Stored provider data remains untrusted even when displayed in an old case.
  if (!/^https?:\/\//i.test(url)) return <span>{children || url}</span>;
  return <a href={url} target="_blank" rel="noopener noreferrer"
    className="inline-flex max-w-full items-center gap-1 break-all text-xs text-accent-300 hover:underline">
    {children || url} <ExternalLink className="h-3 w-3 shrink-0" />
  </a>;
}

function useCaseItems<T>(caseId: string, resource: string) {
  return useInfiniteQuery({
    queryKey: ["osint", caseId, resource],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api.get<T[]>(`/osint/cases/${caseId}/${resource}?limit=${pageSize}&offset=${pageParam}`),
    getNextPageParam: (last, pages) => last.length === pageSize ? pages.length * pageSize : undefined,
  });
}

function More({ hasMore, pending, load }: { hasMore: boolean; pending: boolean; load: () => void }) {
  return hasMore ? <button onClick={load} disabled={pending}
    className="mt-4 rounded-md border border-ink-700 px-4 py-2 text-xs disabled:opacity-40">
    {pending ? "Загрузка…" : "Показать ещё"}
  </button> : null;
}

function Artifacts({ caseId }: { caseId: string }) {
  const query = useCaseItems<OsintArtifact>(caseId, "artifacts");
  const items = query.data?.pages.flat() || [];
  return <div className="space-y-3">
    <ErrorNotice error={query.error} />
    {query.isPending ? <p className="text-sm text-neutral-500">Загрузка материалов…</p> :
      !items.length && !query.error && <p className="py-8 text-center text-sm text-neutral-500">
        Материалов пока нет. Запустите поиск или добавьте наблюдение с источником.
      </p>}
    {items.map((a) => <article key={a.id} className="rounded-lg border border-ink-700 bg-ink-900 p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-neutral-500">
        <span className="rounded bg-accent-500/10 px-2 py-0.5 text-accent-300">{providerLabels[a.provider] || a.provider}</span>
        <span className="break-all">{a.target}</span><time className="ml-auto">Получено {date(a.created_at)}</time>
      </div>
      <h3 className="break-words text-sm font-medium">{a.title}</h3>
      <p className="my-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-neutral-400">{a.summary}</p>
      <SourceLink url={a.source_url} />
      {Object.keys(a.data).length > 0 && <details className="mt-3 text-xs text-neutral-400">
        <summary className="cursor-pointer">Данные источника (JSON)</summary>
        <pre className="mt-2 max-h-80 overflow-auto rounded bg-ink-950 p-3 text-[11px]">{JSON.stringify(a.data, null, 2)}</pre>
      </details>}
    </article>)}
    <More hasMore={query.hasNextPage} pending={query.isFetchingNextPage} load={() => query.fetchNextPage()} />
  </div>;
}

function Timeline({ caseId }: { caseId: string }) {
  const query = useCaseItems<OsintLookup>(caseId, "lookups");
  const items = query.data?.pages.flat() || [];
  return <div>
    <ErrorNotice error={query.error} />
    {query.isPending && <p className="text-sm text-neutral-500">Загрузка истории…</p>}
    {!query.isPending && !items.length && !query.error && <p className="py-8 text-center text-sm text-neutral-500">Запросов ещё не было.</p>}
    <ol className="space-y-3">
      {items.map((run) => <li key={run.id} className="rounded-lg border border-ink-700 bg-ink-900 p-4">
        <div className="flex flex-wrap gap-2 text-sm">
          <span>{providerLabels[run.provider]}</span>
          <span className="break-all text-neutral-400">{run.target}</span>
          <span className={`ml-auto text-xs ${run.status === "error" ? "text-amber-300" : "text-emerald-300"}`}>
            {run.status === "ok" ? "Готово" : run.status === "empty" ? "Нет данных" : "Ошибка"}
          </span>
        </div>
        <p className="mt-2 text-xs text-neutral-400">{run.error ||
          (run.status === "empty" ? "В базе источника нет записей. Это не подтверждает отсутствие рисков." :
            `Добавлено: ${run.artifact_count} · Повторов: ${run.duplicate_count}`)}</p>
        <time className="mt-2 block text-[11px] text-neutral-600">{date(run.created_at)}</time>
      </li>)}
    </ol>
    <More hasMore={query.hasNextPage} pending={query.isFetchingNextPage} load={() => query.fetchNextPage()} />
  </div>;
}

function Sources({ caseId }: { caseId: string }) {
  const query = useCaseItems<OsintSource>(caseId, "sources");
  const items = query.data?.pages.flat() || [];
  return <div>
    <ErrorNotice error={query.error} />
    {query.isPending && <p className="text-sm text-neutral-500">Загрузка источников…</p>}
    {!query.isPending && !items.length && !query.error && <p className="py-8 text-center text-sm text-neutral-500">Источники появятся вместе с материалами.</p>}
    <ul className="space-y-3">{items.map((s) => <li key={`${s.provider}:${s.source_url}`}
      className="rounded-lg border border-ink-700 bg-ink-900 p-4">
      <p className="mb-2 text-xs text-neutral-400">{providerLabels[s.provider] || s.provider} · Материалов: {s.artifact_count}</p>
      <SourceLink url={s.source_url} />
    </li>)}</ul>
    <More hasMore={query.hasNextPage} pending={query.isFetchingNextPage} load={() => query.fetchNextPage()} />
  </div>;
}

function CaseWorkspace({ item, onDelete }: { item: OsintCase; onDelete: () => void }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState("artifacts");
  const [provider, setProvider] = useState<IntelProviderName>("urlscan");
  const [target, setTarget] = useState(item.subject_type === "domain" ? item.subject : "");
  const [showNote, setShowNote] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [note, setNote] = useState({ title: "", summary: "", source_url: "" });
  const providers = useQuery({ queryKey: ["intelligence"], queryFn: () => api.get<IntelProvider[]>("/integrations/intelligence") });
  const selectedProvider = providers.data?.find((p) => p.provider === provider);
  const missingKey = selectedProvider?.key_required && !selectedProvider.configured;
  const invalidate = () => qc.invalidateQueries({ queryKey: ["osint"] });
  const lookup = useMutation({
    mutationFn: () => api.post<OsintLookup>(`/osint/cases/${item.id}/lookups`, { provider, target }),
    onSuccess: (run) => { invalidate(); setTab(run.status === "ok" ? "artifacts" : "lookups"); },
  });
  const addNote = useMutation({
    mutationFn: () => api.post(`/osint/cases/${item.id}/artifacts`, note),
    onSuccess: () => { invalidate(); setNote({ title: "", summary: "", source_url: "" }); setShowNote(false); setTab("artifacts"); },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/osint/cases/${item.id}`),
    onSuccess: () => { invalidate(); onDelete(); },
  });
  function submitLookup(e: FormEvent) {
    e.preventDefault();
    if (target.trim() && !missingKey && !lookup.isPending && !remove.isPending) lookup.mutate();
  }
  return <div className="flex h-full min-w-0 flex-col">
    <div className="shrink-0 space-y-3 border-b border-ink-700 p-4 md:p-5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[11px] text-neutral-500">{subjectLabels[item.subject_type]} · создан {date(item.created_at)}</p>
          <h2 className="mt-1 break-words text-lg font-medium">{item.subject}</h2>
        </div>
        <button onClick={() => setConfirmDelete(!confirmDelete)} disabled={lookup.isPending || remove.isPending}
          aria-label="Удалить кейс" className="p-1 text-neutral-500 hover:text-red-300 disabled:opacity-40"><Trash2 className="h-4 w-4" /></button>
      </div>
      {confirmDelete && <div className="flex flex-wrap items-center gap-3 rounded bg-red-500/10 p-3 text-xs text-red-200">
        Удалить кейс и все его материалы?
        <button onClick={() => remove.mutate()} disabled={remove.isPending} className="font-semibold underline">Удалить</button>
        <button onClick={() => setConfirmDelete(false)}>Отмена</button>
      </div>}
      <ErrorNotice error={remove.error} />
      <form onSubmit={submitLookup} className="space-y-2">
        <div className="grid gap-2 xl:grid-cols-[1fr_160px_auto]">
          <label className="block text-xs text-neutral-400">Домен или публичный IP
            <input className={`${inputStyle} mt-1`} value={target} onChange={(e) => setTarget(e.target.value)}
              required maxLength={500} disabled={lookup.isPending} placeholder="example.com" />
          </label>
          <label className="block text-xs text-neutral-400">Источник
            <select className={`${inputStyle} mt-1`} value={provider} disabled={lookup.isPending}
              onChange={(e) => setProvider(e.target.value as IntelProviderName)}>
              {(["urlscan", "shodan", "virustotal", "securitytrails"] as const).map((p) => <option key={p} value={p}>{providerLabels[p]}</option>)}
            </select>
          </label>
          <button type="submit" className={`${buttonStyle} self-end`} disabled={!target.trim() || !!missingKey || lookup.isPending || remove.isPending || providers.isPending || !!providers.error}>
            {lookup.isPending ? "Поиск в источнике…" : "Пассивный поиск"}
          </button>
        </div>
        <p className="text-[11px] leading-relaxed text-neutral-500">
          {item.subject_type === "domain" ? "Запрос к базе провайдера. Трафик к цели не отправляется." :
            "Для человека или компании укажите связанный домен/IP. Поиск по имени пока не подключён; доступен сбор материалов вручную."}
        </p>
        {missingKey && <p className="text-xs text-amber-300">Нужен ключ {selectedProvider?.name}. <Link href="/settings/integrations" className="underline">Настроить источник</Link></p>}
      </form>
      <ErrorNotice error={providers.error || lookup.error} />
      {lookup.data && !lookup.isPending && <p role="status" className={`text-xs ${lookup.data.status === "error" ? "text-amber-300" : "text-emerald-300"}`}>
        {lookup.data.error || (lookup.data.status === "empty" ? "В источнике нет записей." : `Добавлено: ${lookup.data.artifact_count}. Повторов: ${lookup.data.duplicate_count}.`)}
      </p>}
      <button onClick={() => setShowNote(!showNote)} className="flex items-center gap-1 text-xs text-neutral-300">
        <Plus className="h-3.5 w-3.5" /> Добавить материал вручную
      </button>
      {showNote && <form className="space-y-2 rounded-lg border border-ink-700 p-3" onSubmit={(e) => { e.preventDefault(); addNote.mutate(); }}>
        <label className="block text-xs text-neutral-400">Название материала
          <input value={note.title} onChange={(e) => setNote({ ...note, title: e.target.value })} required maxLength={300} className={`${inputStyle} mt-1`} />
        </label>
        <label className="block text-xs text-neutral-400">Наблюдение
          <textarea value={note.summary} onChange={(e) => setNote({ ...note, summary: e.target.value })} required maxLength={8000} rows={3} className={`${inputStyle} mt-1`} />
        </label>
        <label className="block text-xs text-neutral-400">Ссылка на источник
          <input type="url" value={note.source_url} onChange={(e) => setNote({ ...note, source_url: e.target.value })} required maxLength={2048} placeholder="https://…" className={`${inputStyle} mt-1`} />
        </label>
        <ErrorNotice error={addNote.error} />
        <button className={buttonStyle} disabled={addNote.isPending || remove.isPending}>Сохранить материал</button>
      </form>}
      <nav className="flex flex-wrap gap-1" aria-label="Разделы кейса">
        {[["artifacts", `Материалы · ${item.artifact_count}`], ["lookups", `Таймлайн · ${item.lookup_count}`], ["sources", "Источники"]].map(([id, label]) =>
          <button key={id} onClick={() => setTab(id)} aria-current={tab === id ? "page" : undefined}
            className={`rounded-md px-3 py-1.5 text-xs ${tab === id ? "bg-ink-700 text-white" : "text-neutral-500 hover:text-white"}`}>{label}</button>)}
      </nav>
    </div>
    <div key={tab} className="pane-enter min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
      {tab === "artifacts" ? <Artifacts caseId={item.id} /> : tab === "lookups" ? <Timeline caseId={item.id} /> : <Sources caseId={item.id} />}
    </div>
  </div>;
}

function CasePanel({ id, onDelete }: { id: string; onDelete: () => void }) {
  const query = useQuery({ queryKey: ["osint", id], queryFn: () => api.get<OsintCase>(`/osint/cases/${id}`) });
  if (query.isPending) return <p className="p-6 text-sm text-neutral-500">Загрузка кейса…</p>;
  if (query.error) return <div className="p-6"><ErrorNotice error={query.error} /></div>;
  return <CaseWorkspace item={query.data} onDelete={onDelete} />;
}

export function OsintDomain() {
  const qc = useQueryClient();
  const [view, setView] = useState("chat");
  useEffect(() => { const open = () => setView("chat"); window.addEventListener("layla:open-chat", open); return () => window.removeEventListener("layla:open-chat", open); }, []);
  const [selected, setSelected] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<{ subject: string; subject_type: SubjectType }>({ subject: "", subject_type: "domain" });
  const query = useInfiniteQuery({
    queryKey: ["osint", "cases", q, filter], initialPageParam: 0,
    queryFn: ({ pageParam }) => api.get<OsintCase[]>(`/osint/cases?limit=${pageSize}&offset=${pageParam}&q=${encodeURIComponent(q)}${filter ? `&subject_type=${filter}` : ""}`),
    getNextPageParam: (last, pages) => last.length === pageSize ? pages.length * pageSize : undefined,
  });
  const cases = query.data?.pages.flat() || [];
  const create = useMutation({
    mutationFn: () => api.post<OsintCase>("/osint/cases", form),
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ["osint"] }); setSelected(item.id);
      setShowCreate(false); setForm({ subject: "", subject_type: "domain" }); setQ(""); setFilter("");
    },
  });
  return <div className="domain-workspace flex h-full min-w-0 flex-col">
    <header className="workspace-toolbar">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent-500/10 text-accent-300"><Search className="h-5 w-5" /></span><div className="workspace-title"><h1>OSINT</h1><p>Исследования по открытым источникам</p></div>
      <nav className="segmented-control" aria-label="Режим OSINT">
      {[["cases", "Кейсы"], ["chat", "Чат"]].map(([id, label]) => <button key={id} onClick={() => setView(id)}
        className="segment" aria-pressed={view === id}>{label}</button>)}</nav>
      <Link href="/settings/integrations" className="secondary-button text-xs">Источники</Link>
    </header>
    {view === "chat" ? <div className="min-h-0 flex-1"><ChatPanel domain="osint" /></div> :
      <div className="domain-columns" data-detail={!!selected}>
        <aside className="domain-list">
          <div className="space-y-3 border-b border-ink-700 p-4">
            <div className="flex items-center gap-2"><h2 className="text-sm font-medium">OSINT-кейсы</h2>
              <button onClick={() => qc.invalidateQueries({ queryKey: ["osint"] })} aria-label="Обновить кейсы"
                className="ml-auto text-neutral-500 hover:text-white"><RefreshCw className={`h-3.5 w-3.5 ${query.isFetching ? "animate-spin" : ""}`} /></button>
            </div>
            <input aria-label="Поиск кейсов" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск кейсов…" className={inputStyle} />
            <select aria-label="Тип кейса" value={filter} onChange={(e) => setFilter(e.target.value)} className={inputStyle}>
              <option value="">Все типы</option>{Object.entries(subjectLabels).map(([id, name]) => <option value={id} key={id}>{name}</option>)}
            </select>
            <button onClick={() => setShowCreate(!showCreate)} className={`${buttonStyle} flex w-full items-center justify-center gap-1`}><Plus className="h-3.5 w-3.5" /> Новый кейс</button>
            {showCreate && <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
              <label className="block text-xs text-neutral-400">Тип нового кейса
                <select value={form.subject_type} onChange={(e) => setForm({ ...form, subject_type: e.target.value as SubjectType })} className={`${inputStyle} mt-1`}>
                  {Object.entries(subjectLabels).map(([id, name]) => <option value={id} key={id}>{name}</option>)}
                </select>
              </label>
              <label className="block text-xs text-neutral-400">Объект исследования
                <input value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} required maxLength={500}
                  placeholder={form.subject_type === "domain" ? "example.com" : "Имя / название"} className={`${inputStyle} mt-1`} />
              </label>
              <ErrorNotice error={create.error} />
              <button disabled={!form.subject.trim() || create.isPending} className={buttonStyle}>{create.isPending ? "Создание…" : "Создать кейс"}</button>
            </form>}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <ErrorNotice error={query.error} />
            {query.isPending && <p className="p-3 text-xs text-neutral-500">Загрузка…</p>}
            {!query.isPending && !query.error && !cases.length && <p className="p-4 text-center text-xs leading-relaxed text-neutral-500">{q || filter ? "Кейсы не найдены." : "Кейсов пока нет. Создайте первый, чтобы собирать данные из открытых источников."}</p>}
            <ul className="space-y-1">{cases.map((item) => <li key={item.id}>
              <button onClick={() => setSelected(item.id)} aria-current={selected === item.id ? "true" : undefined}
                className={`w-full rounded-lg border p-3 text-left ${selected === item.id ? "border-accent-500/40 bg-accent-500/10" : "border-transparent hover:bg-ink-800"}`}>
                <span className="block truncate text-sm">{item.subject}</span>
                <span className="mt-1 block text-[11px] text-neutral-500">{subjectLabels[item.subject_type]} · {item.artifact_count} материалов</span>
              </button>
            </li>)}</ul>
            <More hasMore={query.hasNextPage} pending={query.isFetchingNextPage} load={() => query.fetchNextPage()} />
          </div>
        </aside>
        <div className="domain-detail">
          {selected ? <>
            <button onClick={() => setSelected(null)} className="domain-back items-center gap-2 px-4 py-3 text-sm text-neutral-400"><ArrowLeft className="h-4 w-4" /> К списку кейсов</button>
            <div className="min-h-0 flex-1"><CasePanel key={selected} id={selected} onDelete={() => setSelected(null)} /></div>
          </> : <div className="grid h-full place-items-center p-8"><div className="max-w-md text-center">
            <Globe2 className="mx-auto mb-4 h-10 w-10 text-accent-300/70" />
            <h2 className="text-lg font-medium">Исследование по открытым источникам</h2>
            <p className="mt-2 text-sm leading-relaxed text-neutral-500">Создайте или выберите кейс. Собирайте материалы, сравнивайте данные источников и сохраняйте историю запросов.</p>
          </div></div>}
        </div>
      </div>}
  </div>;
}
