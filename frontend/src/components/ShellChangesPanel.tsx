"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type FileChange } from "@/lib/api";
import { useAuth } from "@/store/auth";
import { FileDiff } from "@/components/FileDiff";
import { confirmAction } from "@/components/ConfirmDialog";

type Pending = { id?: string | null; changes: FileChange[]; blocked?: Record<string, string>; error?: string };
type Run = { id: string; status: string; rolled_back: boolean; paths: string[] };

export function ShellChangesPanel({ projectId, running, onFileChange }: {
  projectId: string; running: boolean; onFileChange?: (change: FileChange) => void;
}) {
  const owner = useAuth(state => state.user?.id);
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useQuery({ queryKey: ["shell-changes", owner, projectId], enabled: !running,
    queryFn: () => api.get<Pending>(`/projects/${projectId}/command-changes`), retry: false });
  const history = useQuery({ queryKey: ["shell-history", owner, projectId], enabled: !running,
    queryFn: () => api.get<Run[]>(`/projects/${projectId}/shell-history`), retry: false });
  async function act(action: "apply" | "discard" | "rollback", runId?: string) {
    if (action === "discard" && !(await confirmAction("Отклонить сохранённые результаты команды? Следующая синхронизация вернёт исходные файлы проекта в песочницу."))) return;
    if (action === "rollback" && !(await confirmAction("Вернуть файлы к состоянию до этой команды? При наличии более поздних правок откат будет отклонён."))) return;
    setBusy(true); setError(null);
    try {
      const result = action === "rollback"
        ? await api.post<{ restored: string[] }>(`/projects/${projectId}/shell-history/${runId}/rollback`, {})
        : await api.post<{ changes?: FileChange[]; error?: string }>(`/projects/${projectId}/command-changes`, { action, change_id: pending.data?.id });
      if ("error" in result && result.error) setError(result.error);
      if ("changes" in result) result.changes?.forEach(change => onFileChange?.(change));
      if ("restored" in result) result.restored.forEach(path => onFileChange?.({ path, operation: "edit", diff: "", before_sha256: null, after_sha256: null }));
      await Promise.all([pending.refetch(), history.refetch(), qc.invalidateQueries({ queryKey: ["project-files", projectId] })]);
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось выполнить действие"); }
    finally { setBusy(false); }
  }
  return <div className="space-y-2 text-xs">
    {pending.data?.id && <details open className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-3">
      <summary className="cursor-pointer text-amber-200">Сохранённые изменения команды · {pending.data.changes.length}</summary>
      <p className="mt-2 text-neutral-400">Новые команды ждут решения по этому пакету. Параллельные правки проекта сохраняются.</p>
      {pending.data.error && <p className="mt-2 text-red-300">{pending.data.error}</p>}
      {pending.data.changes.map(change => <FileDiff key={change.path} change={change} />)}
      {Object.entries(pending.data.blocked || {}).map(([path, reason]) => <p key={path} className="mt-1 break-all text-amber-300">{path}: {reason}</p>)}
      <div className="mt-3 flex gap-2">
        <button disabled={busy || running || !!pending.data.error} onClick={() => void act("apply")} className="primary-button !px-3 !py-1.5">Применить</button>
        <button disabled={busy || running} onClick={() => void act("discard")} className="secondary-button !px-3 !py-1.5">Отклонить пакет</button>
      </div>
    </details>}
    {!!history.data?.length && <details className="rounded-xl border border-ink-700 p-3">
      <summary className="cursor-pointer text-neutral-400">История изменений команд · {history.data.length}</summary>
      {history.data.map(run => <div key={run.id} className="mt-2 flex items-center gap-2 border-t border-ink-700 pt-2">
        <span className="min-w-0 flex-1 truncate text-neutral-400" title={run.paths.join("\n")}>{run.paths.join(", ")}</span>
        <button disabled={busy || running || run.rolled_back || run.status === "running"} onClick={() => void act("rollback", run.id)} className="secondary-button !px-2 !py-1">{run.rolled_back ? "Откат выполнен" : "Откатить"}</button>
      </div>)}
    </details>}
    {error && <p role="alert" className="text-red-300">{error}</p>}
  </div>;
}
