"use client";

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, GitBranch, History } from "lucide-react";
import { api, downloadCodeWorkspace } from "@/lib/api";
import { confirmAction } from "@/components/ConfirmDialog";

type Workspace = { id: string; name: string; branch: string; status: string; active: boolean; available: boolean; published: boolean; path: string; pull_request?: { url: string; number: number } };
type Entry = { turn_id: string; sequence: number; status: string; active: boolean; message: { role: string; content?: string; tool_calls?: unknown[]; tool_call_id?: string } };

export function CodeLeadPanel({ chatId, scope, running, canApply }: { chatId: string; scope: string; running: boolean; canApply: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("draft");
  const [baseRef, setBaseRef] = useState("");
  const [prTitle, setPrTitle] = useState("");
  const [prBase, setPrBase] = useState("");
  const importInput = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [path, setPath] = useState(".");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const workspaces = useQuery({ queryKey: ["code-workspaces", scope, chatId],
    queryFn: () => api.get<{ workspaces: Workspace[] }>(`/chats/${chatId}/workspaces`), refetchInterval: running ? 1500 : false, retry: false });
  const active = workspaces.data?.workspaces.find(item => item.active);
  const diff = useQuery({ queryKey: ["code-workspace-diff", scope, chatId, selected],
    queryFn: () => api.get<{ diff: string; truncated: boolean }>(`/chats/${chatId}/workspaces/${selected}/diff`), enabled: open && !!selected, retry: false,
    refetchInterval: open && selected === active?.id && running ? 2000 : false });
  const contents = useQuery({ queryKey: ["code-workspace-files", scope, chatId, selected, path],
    queryFn: () => api.get<{ files?: { path: string; name: string; is_dir: boolean }[]; content?: string }>(`/chats/${chatId}/workspaces/${selected}/files?path=${encodeURIComponent(path)}`),
    enabled: open && !!selected, retry: false, refetchInterval: open && selected === active?.id && running ? 2000 : false });
  async function control(action: "enter" | "resume" | "exit" | "publish" | "pr", id?: string, remove = false) {
    if (busy) return;
    if (remove && !(await confirmAction("Удалить активную копию вместе с её файлами и локальными коммитами? Исходный проект сохранится."))) return;
    if (action === "publish" && !(await confirmAction("Опубликовать коммиты этой копии в её отдельную ветку настроенного репозитория?"))) return;
    setBusy(true); setError(null);
    try {
      await api.post(`/chats/${chatId}/workspaces/${action}`, action === "enter" ? { name, ...(baseRef.trim() ? { base_ref: baseRef.trim() } : {}) } : action === "pr" ? { workspace_id: id, title: prTitle.trim(), base: prBase.trim() || null, draft: true } : action === "exit" ? { action: remove ? "remove" : "keep", discard_changes: remove } : { workspace_id: id });
      await workspaces.refetch();
      await qc.invalidateQueries({ queryKey: ["code-state", scope, chatId] });
      await qc.invalidateQueries({ queryKey: ["code-workspace-diff", scope, chatId] });
      await qc.invalidateQueries({ queryKey: ["code-workspace-files", scope, chatId] });
      if (remove) setSelected(null);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось выполнить действие"); }
    finally { setBusy(false); }
  }
  async function transfer(action: "fork" | "import" | "export", file?: File) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      if (action === "export") {
        const data = await api.get<unknown>(`/chats/${chatId}/session/export`);
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
        const link = document.createElement("a"); link.href = url; link.download = "code-session.json";
        document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        return;
      }
      if (action === "import" && (!file || file.size > 10_000_000)) throw new Error("Выберите JSON/JSONL до 10 МБ");
      const chat = await api.post<{ id: string; domain: string; project_id: string }>(`/chats/${chatId}/session/${action}`,
        action === "fork" ? { title: "Копия сессии" } : { title: file!.name, text: await file!.text() });
      await qc.invalidateQueries({ queryKey: ["chats"] });
      window.dispatchEvent(new CustomEvent("layla:open-chat", { detail: chat }));
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось перенести сессию"); }
    finally { setBusy(false); if (importInput.current) importInput.current.value = ""; }
  }
  return <>
    <details onToggle={e => setOpen(e.currentTarget.open)} className="rounded-xl border border-ink-700/70 bg-ink-950/20 p-3 text-xs">
      <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
        <GitBranch className="h-4 w-4 text-accent-300" /><span className="min-w-0 flex-1 truncate text-neutral-300">{active ? `Агент в копии · ${active.name}` : "Агент в исходном проекте"}</span><ChevronDown className="h-3.5 w-3.5 text-neutral-500" />
      </summary>
      <div className="mt-3 space-y-3 border-t border-ink-700 pt-3">
        {workspaces.isError && <p role="alert" className="text-red-300">Не удалось загрузить рабочие копии</p>}
        <p className="text-neutral-500">Редактор, терминал и Git следуют за рабочей копией этого чата. Несохранённые черновики остаются в своей копии. Новые запросы продолжаются здесь.</p>
        {active ? <div className="space-y-2">
          <p className="break-all font-mono text-neutral-400">{active.branch}</p>
          <button disabled={busy || running || !canApply} onClick={() => void control("exit")} className="secondary-button !px-3 !py-1.5">В исходный проект · сохранить копию</button>
          <button disabled={busy || running || !canApply} onClick={() => void control("exit", undefined, true)} className="secondary-button !px-3 !py-1.5">Удалить активную копию</button>
        </div> : <form onSubmit={e => { e.preventDefault(); void control("enter"); }} className="flex flex-wrap gap-2">
          <input aria-label="Имя рабочей копии" value={name} maxLength={60} onChange={e => setName(e.target.value)} className="min-w-0 flex-1 rounded-lg bg-ink-900 p-2" />
          <input aria-label="Исходная ветка или commit (необязательно)" value={baseRef} onChange={e => setBaseRef(e.target.value)} placeholder="Ветка или commit (необязательно)" maxLength={200} className="min-w-0 flex-1 rounded-lg bg-ink-900 p-2" />
          <button disabled={busy || running || !canApply || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$/.test(name)} className="secondary-button !px-3 !py-1.5">Создать копию</button>
        </form>}
        {workspaces.data?.workspaces.map(item => <div key={item.id} className="space-y-2 rounded-lg border border-ink-700 p-2">
          <p className="break-all text-neutral-300">{item.name} · {item.active ? "активна" : item.status === "kept" ? "сохранена" : item.status === "removed" ? "удалена" : item.status}{item.published ? " · опубликована" : ""}</p>
          <p className="break-all font-mono text-neutral-500">{item.branch}</p>
          {item.available && <div className="flex flex-wrap gap-2">
            <button onClick={() => { setSelected(selected === item.id ? null : item.id); setPath("."); }} className="secondary-button !px-2 !py-1">Файлы и диф</button>
            {!active && item.status === "kept" && <button disabled={busy || running || !canApply} onClick={() => void control("resume", item.id)} className="secondary-button !px-2 !py-1">Продолжить здесь</button>}
            <button disabled={busy || running || !canApply} onClick={() => void control("publish", item.id)} className="secondary-button !px-2 !py-1">Опубликовать ветку</button>
            <button disabled={busy || running || !canApply || !prTitle.trim()} onClick={() => void control("pr", item.id)} className="secondary-button !px-2 !py-1">Создать draft PR</button>
            {item.pull_request && <a href={item.pull_request.url} target="_blank" rel="noreferrer" className="secondary-button !px-2 !py-1">PR #{item.pull_request.number}</a>}
            <button onClick={() => void downloadCodeWorkspace(chatId, item.id, item.name).catch(e => setError(e.message))} className="secondary-button !px-2 !py-1">Скачать ZIP</button>
          </div>}
        </div>)}
        <div className="flex flex-wrap gap-2"><input value={prTitle} onChange={e => setPrTitle(e.target.value)} placeholder="Название PR для выбранной копии" maxLength={250} className="min-w-0 flex-1 rounded-lg bg-ink-900 p-2" />
          <input value={prBase} onChange={e => setPrBase(e.target.value)} placeholder="Базовая ветка (по умолчанию основная)" maxLength={200} className="min-w-0 flex-1 rounded-lg bg-ink-900 p-2" /></div>
        <div className="flex flex-wrap gap-2"><button disabled={busy || running} onClick={() => void transfer("fork")} className="secondary-button !px-2 !py-1">Разветвить сессию и файлы</button>
          <button disabled={busy} onClick={() => importInput.current?.click()} className="secondary-button !px-2 !py-1">Импорт JSON/JSONL</button>
          <button disabled={busy} onClick={() => void transfer("export")} className="secondary-button !px-2 !py-1">Экспорт сессии</button>
          <input ref={importInput} type="file" accept=".json,.jsonl" className="hidden" onChange={e => { if (e.target.files?.[0]) void transfer("import", e.target.files[0]); }} /></div>
        {selected && <div className="space-y-2 rounded-lg bg-ink-950/50 p-2">
          <div className="flex gap-2"><input aria-label="Путь внутри копии" value={path} onChange={e => setPath(e.target.value || ".")} className="min-w-0 flex-1 rounded bg-ink-900 p-2 font-mono" />
            <button onClick={() => setPath(".")} className="secondary-button !px-2 !py-1">Корень</button></div>
          {contents.isError && <p className="text-red-300">Файл недоступен или не является текстовым</p>}
          <div className="flex max-h-40 flex-wrap gap-2 overflow-auto">{contents.data?.files?.map(file => <button key={file.path} onClick={() => setPath(file.path)} className="rounded bg-ink-900 px-2 py-1 text-neutral-400">{file.is_dir ? "📁 " : ""}{file.name}</button>)}</div>
          {contents.data?.content !== undefined && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-neutral-300">{contents.data.content}</pre>}
          <details><summary className="cursor-pointer text-neutral-400">Диф со времени создания копии</summary>
            {diff.isError && <p className="text-red-300">Не удалось загрузить диф</p>}
            <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-neutral-400">{diff.data?.diff || "Изменений нет"}</pre>
            {diff.data?.truncated && <p className="text-neutral-500">Большой диф сокращён</p>}
          </details>
        </div>}
        <LeadJournal key={`${scope}:${chatId}`} chatId={chatId} scope={scope} running={running} />
        {error && <p role="alert" className="text-red-300">{error}</p>}
      </div>
    </details>
  </>;
}

function LeadJournal({ chatId, scope, running }: { chatId: string; scope: string; running: boolean }) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const journal = useQuery({ queryKey: ["code-session", scope, chatId, offset],
    queryFn: () => api.get<{ entries: Entry[]; next_offset: number | null }>(`/chats/${chatId}/session?offset=${offset}&limit=25`), enabled: open,
    refetchInterval: open && running ? 2000 : false, retry: false });
  return <details onToggle={e => setOpen(e.currentTarget.open)} className="rounded-xl border border-ink-700/70 p-3 text-xs">
    <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden"><History className="h-4 w-4 text-accent-300" /><span className="flex-1 text-neutral-300">Сохранённая сессия ведущего</span><ChevronDown className="h-3.5 w-3.5 text-neutral-500" /></summary>
    <div className="mt-3 space-y-2">
      {journal.isPending && <p className="text-neutral-500">Загружаю журнал…</p>}
      {journal.isError && <p role="alert" className="text-red-300">Не удалось загрузить журнал</p>}
      {journal.data?.entries.map(entry => <details key={`${entry.turn_id}:${entry.sequence}`} className="rounded-lg bg-ink-900 p-2">
        <summary className="cursor-pointer text-neutral-400">{entry.message.role}{entry.message.tool_call_id ? ` · ${entry.message.tool_call_id}` : ""}{!entry.active ? " · исключён откатом" : entry.status === "interrupted" ? " · ход прерван" : ""}</summary>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-neutral-300">{entry.message.content}{entry.message.tool_calls ? "\n" + JSON.stringify(entry.message.tool_calls, null, 2) : ""}</pre>
      </details>)}
      <div className="flex gap-2"><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 25))} className="secondary-button !px-2 !py-1">Назад</button>
        <button disabled={journal.data?.next_offset == null} onClick={() => setOffset(journal.data?.next_offset || 0)} className="secondary-button !px-2 !py-1">Далее</button></div>
    </div>
  </details>;
}
