"use client";
import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Loader2 } from "lucide-react";
import { api, type Job } from "@/lib/api";
import { useAuth } from "@/store/auth";

const labels: Record<string, string> = { code: "Код", design: "Дизайн", pentest: "Пентест", osint: "OSINT" };
const states = { queued: "В очереди", running: "Работает", done: "Готово", error: "Ошибка", cancelled: "Остановлено" };
export function ActivityPanel() {
  const owner = useAuth(s => s.user?.id);
  const router = useRouter();
  const qc = useQueryClient();
  const menu = useRef<HTMLDetailsElement>(null);
  const previous = useRef(new Map<string, string>());
  const { data = [], isError } = useQuery({ queryKey: ["jobs", owner], enabled: !!owner, queryFn: () => api.get<Job[]>("/jobs?limit=100"), refetchInterval: 1500, refetchIntervalInBackground: true });
  const running = data.filter(j => j.status === "running" || j.status === "queued");
  useEffect(() => {
    for (const job of data) {
      if (previous.current.get(job.id) !== job.status) {
        const chat = job.chat_id || job.result.chat_id;
        if (chat) qc.invalidateQueries({ queryKey: ["chat", owner, chat] });
        if (job.status === "done") { qc.invalidateQueries({ queryKey: ["designs"] }); qc.invalidateQueries({ queryKey: ["findings"] }); }
      }
      previous.current.set(job.id, job.status);
    }
  }, [data, owner, qc]);
  function open(job: Job) {
    const chat = job.chat_id || job.result.chat_id;
    const project = job.result.project_id;
    if (menu.current) menu.current.open = false;
    const query = new URLSearchParams();
    if (typeof chat === "string") {
      query.set("chat", chat);
      try {
        localStorage.setItem(`layla:chat:${owner}:${job.domain}:`, chat);
        if (typeof project === "string") {
          localStorage.setItem(`layla:chat:${owner}:${job.domain}:${project}`, chat);
          if (job.domain === "code") localStorage.setItem(`layla:project:${owner}`, project);
        }
      } catch { /* Storage can be unavailable. */ }
    } else {
      query.set("view", "workspace");
      if (typeof job.result.design_id === "string") query.set("design", job.result.design_id);
    }
    if (typeof project === "string") query.set("project", project);
    router.push(`/${job.domain}?${query}`);
    if (!chat) window.dispatchEvent(new CustomEvent("layla:open-workspace", { detail: { domain: job.domain, design_id: job.result.design_id } }));
    if (chat) window.dispatchEvent(new CustomEvent("layla:open-chat", { detail: { id: chat, project_id: project, domain: job.domain } }));
  }
  return <details ref={menu} className="relative">
    <summary aria-label="Прогресс задач" className="flex cursor-pointer list-none items-center gap-2 rounded-full border border-ink-600/60 px-3 py-2 text-xs [&::-webkit-details-marker]:hidden">{running.length ? <Loader2 className="h-4 w-4 animate-spin text-accent-300" /> : <Activity className="h-4 w-4 text-neutral-400" />}<span className="hidden sm:inline">{isError ? "Нет связи" : "В работе"}</span><span aria-live="polite">{running.length}</span></summary>
    <div className="fixed left-3 right-3 top-20 z-50 max-h-[70dvh] overflow-y-auto rounded-2xl border border-ink-600 bg-ink-900 p-3 shadow-floating sm:absolute sm:left-auto sm:right-0 sm:top-full sm:mt-3 sm:w-96">
      <p className="p-2 text-sm font-semibold">Задачи во всех разделах</p>
      {isError && <p role="alert" className="p-2 text-xs text-red-300">Не удалось обновить прогресс. Повторяем подключение…</p>}
      {!data.length && <p className="p-3 text-sm text-neutral-400">Отправьте сообщение в любом чате — его прогресс появится здесь.</p>}
      {[...running, ...data.filter(j => !running.includes(j))].map(job => <button key={job.id} onClick={() => open(job)} className="mb-2 block w-full rounded-xl bg-ink-800 p-3 text-left hover:bg-ink-700"><span className="flex justify-between gap-2 text-xs text-accent-300"><span>{labels[job.domain] || job.domain}</span><span>{states[job.status]}</span></span><span className="mt-2 block truncate text-sm">{job.title}</span><span className="mt-1 block break-words text-xs text-neutral-400">{job.error || job.steps.at(-1)?.text || "Ожидает начала"}</span>{typeof job.result.verdict === "string" && <span className="mt-2 block whitespace-pre-wrap text-sm">{job.result.verdict}</span>}{job.updated_at && <span className="mt-2 block text-[11px] text-neutral-500">Обновлено {new Date(/[Z+]/.test(job.updated_at) ? job.updated_at : job.updated_at + "Z").toLocaleTimeString()}</span>}</button>)}
    </div>
  </details>;
}
