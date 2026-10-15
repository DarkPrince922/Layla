"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Loader2, Users } from "lucide-react";
import { api, type FileChange } from "@/lib/api";
import { FileDiff } from "@/components/FileDiff";
import { confirmAction } from "@/components/ConfirmDialog";

export type CodeWorker = {
  id: string; kind: "agent" | "command"; role: string; task: string; status: string;
  report: string; error?: string | null; detached: boolean; workspace_removed: boolean;
  changes: FileChange[]; applied: Record<string, string | null>;
  events: { id?: string; name: string; path?: string; status: string; note?: string; command?: string; output?: string; error?: string }[];
};
const active = (worker: CodeWorker) => worker.status === "running" || worker.status === "queued";
const statusText: Record<string, string> = { queued: "в очереди", running: "работает", done: "готов",
  error: "ошибка", interrupted: "прерван", cancelled: "остановлен" };

export function CodeWorkerPanel({ workers, chatId, scope, parentRunning, canApply, refresh, onResume }: {
  workers: CodeWorker[]; chatId: string; scope: string; parentRunning: boolean; canApply: boolean;
  refresh: () => Promise<unknown>; onResume: (text: string) => Promise<void>;
}) {
  if (!workers.length) return null;
  const working = workers.filter(active).length;
  return <details className="rounded-xl border border-accent-500/20 bg-accent-500/5 p-3 text-xs">
    <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
      {working ? <Loader2 className="h-4 w-4 animate-spin text-accent-300" /> : <Users className="h-4 w-4 text-accent-300" />}
      <span className="flex-1 text-neutral-300">Воркеры · {working ? `работают ${working} из 3` : `всего ${workers.length}`}</span>
      <ChevronDown className="h-3.5 w-3.5 text-neutral-500" />
    </summary>
    <div className="mt-3 space-y-2 border-t border-ink-700 pt-3">{workers.map(worker => <WorkerCard key={worker.id}
      worker={worker} chatId={chatId} scope={scope} parentRunning={parentRunning} canApply={canApply} refresh={refresh} onResume={onResume} />)}</div>
  </details>;
}

function WorkerCard({ worker, chatId, scope, parentRunning, canApply, refresh, onResume }: {
  worker: CodeWorker; chatId: string; scope: string; parentRunning: boolean; canApply: boolean;
  refresh: () => Promise<unknown>; onResume: (text: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const detail = useQuery({
    queryKey: ["code-worker", scope, chatId, worker.id, worker.status],
    queryFn: () => api.get<{ worker: CodeWorker; output: string; truncated: boolean }>(`/chats/${chatId}/workers/${worker.id}?limit=40000`),
    enabled: open,
    refetchInterval: open && active(worker) ? 1500 : false,
    retry: false,
  });
  const info = detail.data?.worker || worker;
  const pendingChanges = worker.changes.filter(change => !(change.path in worker.applied) || worker.applied[change.path] !== change.after_sha256);
  const latest = worker.events.at(-1);
  async function control(action: "stop" | "resume" | "discard") {
    if (busy) return;
    if (action === "discard" && !(await confirmAction("Удалить рабочую копию воркера?\n" + (pendingChanges.length ? "Неприменённые правки будут удалены. Отчёт и вывод останутся в чате." : "Отчёт и вывод останутся в чате.")))) return;
    setBusy(true); setError(null);
    try {
      await api.post(`/chats/${chatId}/workers/${worker.id}/${action}${action === "discard" ? "?discard_changes=true" : ""}`, {});
      await refresh(); await detail.refetch();
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось выполнить действие"); }
    finally { setBusy(false); }
  }
  return <details onToggle={event => setOpen(event.currentTarget.open)} className="rounded-lg border border-ink-700 bg-ink-950/30 p-3">
    <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
      {active(worker) && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-accent-300" />}
      <span className="min-w-0 flex-1 truncate text-neutral-300">{worker.kind === "command" ? "Команда" : worker.role === "implement" ? "Разработчик" : worker.role === "review" ? "Ревьюер" : "Исследователь"} · {worker.task}</span>
      <span className={worker.status === "error" ? "text-red-300" : "text-neutral-500"}>{statusText[worker.status] || worker.status}</span>
    </summary>
    {active(worker) && latest && <p className="mt-1 truncate text-neutral-500">{latest.name}{latest.path ? ` · ${latest.path}` : ""}</p>}
    <div className="mt-3 space-y-3 border-t border-ink-700 pt-3">
      <p className="whitespace-pre-wrap text-neutral-400">{worker.task}</p>
      <p className="text-neutral-500">ID: {worker.id}{worker.detached ? " · независим от текущего ответа" : ""}</p>
      <div className="flex flex-wrap gap-2">
        {active(worker) && <button disabled={busy} onClick={() => void control("stop")} className="secondary-button !px-3 !py-1.5 text-xs">Остановить</button>}
        {!active(worker) && worker.status !== "done" && !worker.workspace_removed && <button disabled={busy || !canApply} onClick={() => void control("resume")} className="secondary-button !px-3 !py-1.5 text-xs">Продолжить</button>}
        {!active(worker) && worker.role === "implement" && !!pendingChanges.length && !worker.workspace_removed && <button disabled={busy || parentRunning || !canApply} onClick={() => onResume(`Проверь результат воркера ${worker.id}, примени подходящие изменения через worker_apply и проверь основной проект.`)} className="primary-button !px-3 !py-1.5 text-xs">Проверить и применить · {pendingChanges.length}</button>}
        {!active(worker) && !worker.workspace_removed && <button disabled={busy} onClick={() => void control("discard")} className="secondary-button !px-3 !py-1.5 text-xs">Удалить копию</button>}
      </div>
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {info.error && <p className="text-red-300">{info.error}</p>}
      {detail.isPending && open && <p className="text-neutral-500">Загружаю вывод…</p>}
      {detail.isError && <p role="alert" className="text-red-300">Не удалось загрузить вывод</p>}
      {detail.data?.output && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-ink-950/60 p-3 leading-relaxed text-neutral-300">{detail.data.output}</pre>}
      {detail.data?.truncated && <p className="text-neutral-500">Показана часть вывода; агент может прочитать продолжение через worker_output.</p>}
      {!detail.data?.output && info.report && <p className="whitespace-pre-wrap text-neutral-300">{info.report}</p>}
      {!!info.events.length && <details className="rounded-lg border border-ink-700 p-2">
        <summary className="cursor-pointer text-neutral-400">Последние инструменты · {info.events.length}</summary>
        <div className="mt-2 space-y-2">{info.events.map((event, index) => <details key={event.id || index} className="rounded border border-ink-700/60 p-2">
          <summary className="cursor-pointer text-neutral-400">{event.name}{event.path ? ` · ${event.path}` : ""} · {event.status}</summary>
          {event.command && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words text-neutral-500">{event.command}</pre>}
          {event.note && <p className="mt-2 text-neutral-500">{event.note}</p>}
          {event.error && <p className="mt-2 text-red-300">{event.error}</p>}
          {event.output && <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-neutral-300">{event.output}</pre>}
        </details>)}</div>
      </details>}
      {info.changes.map(change => change.diff ? <FileDiff key={change.path} change={change} /> : <p key={change.path} className="text-neutral-500">{change.operation} · {change.path}</p>)}
      {worker.workspace_removed && <p className="text-neutral-500">Рабочая копия удалена; отчёт и вывод сохранены.</p>}
    </div>
  </details>;
}
