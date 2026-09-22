"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Loader2, CheckCircle2, XCircle, X, ChevronRight } from "lucide-react";
import { api, type Job } from "@/lib/api";

const STATUS_LABEL: Record<Job["status"], string> = {
  queued: "в очереди",
  running: "выполняется",
  done: "готово",
  error: "ошибка",
  cancelled: "отменено",
};

function StatusIcon({ status }: { status: Job["status"] }) {
  if (status === "running" || status === "queued")
    return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-indigo-400" />;
  if (status === "done")
    return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />;
  return <XCircle className="h-3.5 w-3.5 shrink-0 text-red-400" />;
}

function Bar({ value }: { value: number }) {
  return (
    <div className="mt-1 h-1 w-full overflow-hidden rounded bg-ink-700">
      <div
        className="h-full rounded bg-indigo-500 transition-all"
        style={{ width: `${Math.round((value || 0) * 100)}%` }}
      />
    </div>
  );
}

// Панель «В работе»: активные фоновые задачи (обновляется опросом), клик —
// подробности с шагами и размышлением модели.
export function JobsPanel() {
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const prevIds = useRef<string[]>([]);

  const { data: jobs = [] } = useQuery({
    queryKey: ["jobs", "active"],
    queryFn: () => api.get<Job[]>("/jobs?active=true"),
    refetchInterval: 2000,
  });

  // Когда активная задача исчезла (завершилась) — обновить результаты доменов.
  useEffect(() => {
    const ids = jobs.map((j) => j.id);
    const finished = prevIds.current.filter((id) => !ids.includes(id));
    if (finished.length) {
      qc.invalidateQueries({ queryKey: ["designs"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    }
    prevIds.current = ids;
  }, [jobs, qc]);

  return (
    <>
      {jobs.length === 0 ? (
        <div className="flex items-center gap-2 px-3 py-2 text-xs text-neutral-500">
          <Activity className="h-3.5 w-3.5" /> Нет активных задач
        </div>
      ) : (
        <div className="space-y-1 px-2">
          {jobs.map((j) => (
            <button
              key={j.id}
              onClick={() => setOpenId(j.id)}
              className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-ink-800"
            >
              <div className="flex items-center gap-1.5">
                <StatusIcon status={j.status} />
                <span className="min-w-0 flex-1 truncate text-xs text-neutral-300">{j.title || j.kind}</span>
                <ChevronRight className="h-3 w-3 shrink-0 text-neutral-600" />
              </div>
              <Bar value={j.progress} />
            </button>
          ))}
        </div>
      )}
      {openId && <JobDetail id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

function JobDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: job } = useQuery({
    queryKey: ["job", id],
    queryFn: () => api.get<Job>(`/jobs/${id}`),
    refetchInterval: (q) => {
      const s = (q.state.data as Job | undefined)?.status;
      return s === "running" || s === "queued" ? 1000 : false;
    },
  });

  async function dismiss() {
    try {
      await api.del(`/jobs/${id}`);
      qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch {
      /* активную убрать нельзя — игнорируем */
    }
    onClose();
  }

  const designId = job?.result?.design_id as string | undefined;
  const finished = job && job.status !== "running" && job.status !== "queued";

  return (
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true">
      <div className="flex-1 bg-black/50" onClick={onClose} />
      <div className="flex h-full w-full max-w-md flex-col border-l border-ink-700 bg-ink-900 shadow-xl">
        <div className="flex items-center gap-2 border-b border-ink-700 px-4 py-3">
          {job && <StatusIcon status={job.status} />}
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-neutral-200">{job?.title || "Задача"}</p>
            <p className="text-[11px] text-neutral-500">
              {job ? `${job.domain} · ${STATUS_LABEL[job.status]} · ${Math.round((job.progress || 0) * 100)}%` : "…"}
            </p>
          </div>
          <button onClick={onClose} aria-label="Закрыть" className="rounded p-1 text-neutral-400 hover:bg-ink-800">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {job && <Bar value={job.progress} />}

          {/* Шаги */}
          {job?.steps?.length ? (
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wide text-neutral-500">Шаги</div>
              <ol className="space-y-1">
                {job.steps.map((s, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-neutral-300">
                    <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-500" />
                    <span className="min-w-0 flex-1">{s.text}</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}

          {/* Размышление модели */}
          {job?.reasoning ? (
            <details open>
              <summary className="cursor-pointer text-[11px] uppercase tracking-wide text-neutral-500">
                Размышление модели
              </summary>
              <div className="mt-1 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-md border border-ink-700 bg-ink-950 p-2 text-xs text-neutral-400">
                {job.reasoning}
              </div>
            </details>
          ) : null}

          {job?.error && (
            <p className="rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-300">{job.error}</p>
          )}

          {designId && (
            <Link
              href="/design"
              onClick={onClose}
              className="inline-block rounded-md bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500"
            >
              Открыть в Design →
            </Link>
          )}
        </div>

        {finished && (
          <div className="border-t border-ink-700 p-3">
            <button
              onClick={dismiss}
              className="w-full rounded-md border border-ink-700 px-3 py-1.5 text-xs text-neutral-400 hover:bg-ink-800"
            >
              Убрать из списка
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
