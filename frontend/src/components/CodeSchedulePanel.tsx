"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock3, Trash2 } from "lucide-react";
import { api } from "@/lib/api";

type Schedule = { id: string; prompt: string; cron: string | null; timezone: string;
  recurring: boolean; mode: string; enabled: boolean; next_run: string;
  last_job_id: string | null; last_error: string | null };

export function CodeSchedulePanel({ chatId }: { chatId: string }) {
  const qc = useQueryClient();
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState("cron");
  const [cron, setCron] = useState("0 9 * * *");
  const [at, setAt] = useState("");
  const [zone, setZone] = useState("UTC");
  const [recurring, setRecurring] = useState(true);
  const [mode, setMode] = useState("confirm");
  useEffect(() => { setZone(Intl.DateTimeFormat().resolvedOptions().timeZone); }, []);
  const endpoint = `/chats/${chatId}/schedules`;
  const state = useQuery({ queryKey: ["code-schedules", chatId],
    queryFn: () => api.get<{ schedules: Schedule[] }>(endpoint), refetchInterval: 15000 });
  const lastJobs = state.data?.schedules.map(s => s.last_job_id).join(",");
  useEffect(() => {
    if (lastJobs) {
      qc.invalidateQueries({ queryKey: ["chat"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    }
  }, [lastJobs, qc]);
  const change = useMutation({ mutationFn: async (action: { id?: string; enabled?: boolean; remove?: boolean }) => {
    if (action.remove) return api.del(`${endpoint}/${action.id}`);
    if (action.id) return api.patch(`${endpoint}/${action.id}`, { enabled: action.enabled });
    return api.post(endpoint, { prompt, mode, timezone: zone,
      cron: kind === "cron" ? cron : null, at: kind === "at" ? new Date(at).toISOString() : null,
      recurring: kind === "cron" && recurring });
  }, onSuccess: (_result, action) => {
    if (!action.id) setPrompt("");
    qc.invalidateQueries({ queryKey: ["code-schedules", chatId] });
  }});
  return <details className="rounded-xl border border-ink-700 p-3 text-xs">
    <summary className="flex cursor-pointer items-center gap-2"><Clock3 className="h-4 w-4" />
      Расписания · {state.data?.schedules.filter(s => s.enabled).length || 0} активных</summary>
    <div className="mt-3 space-y-3">
      {state.data?.schedules.map(s => <div key={s.id} className="rounded-lg border border-ink-700 p-3">
        <p className="whitespace-pre-wrap break-words text-neutral-200">{s.prompt}</p>
        <p className="mt-1 text-neutral-400">{s.cron ? `${s.cron} · ${s.timezone}` : "Разовый запуск"}
          {s.enabled && ` · ${new Date(s.next_run).toLocaleString()}`} · {s.mode}</p>
        {s.last_error && <p role="alert" className="mt-1 text-amber-300">{s.last_error}</p>}
        <div className="mt-2 flex gap-3">
          <button disabled={change.isPending} onClick={() => change.mutate({ id: s.id, enabled: !s.enabled })}
            className="text-accent-300">{s.enabled ? "Приостановить" : "Возобновить"}</button>
          <button disabled={change.isPending} onClick={() => change.mutate({ id: s.id, remove: true })}
            className="ml-auto text-neutral-400" aria-label="Удалить расписание"><Trash2 className="h-3.5 w-3.5" /></button>
        </div>
      </div>)}
      <form className="space-y-2" onSubmit={e => { e.preventDefault(); change.mutate({}); }}>
        <label className="block">Задача<textarea required maxLength={8000} value={prompt} onChange={e => setPrompt(e.target.value)}
          className="mt-1 w-full rounded-lg border border-ink-700 bg-ink-900 p-2" rows={2} /></label>
        <div className="flex flex-wrap gap-2">
          <select aria-label="Тип расписания" value={kind} onChange={e => setKind(e.target.value)} className="rounded bg-ink-800 p-2">
            <option value="cron">По расписанию</option><option value="at">В конкретное время</option>
          </select>
          <select aria-label="Режим выполнения" value={mode} onChange={e => setMode(e.target.value)} className="rounded bg-ink-800 p-2">
            <option value="confirm">С подтверждением</option><option value="auto">Авто</option>
            <option value="plan">План</option><option value="review">Ревью</option>
          </select>
        </div>
        {kind === "cron" ? <>
          <label className="block">Cron: минута час день месяц день недели
            <input required value={cron} onChange={e => setCron(e.target.value)} className="mt-1 w-full rounded bg-ink-800 p-2 font-mono" /></label>
          <label className="block">Часовой пояс<input required value={zone} onChange={e => setZone(e.target.value)}
            className="ml-2 rounded bg-ink-800 p-2" placeholder="Europe/Moscow" /></label>
          <label className="flex gap-2"><input type="checkbox" checked={recurring} onChange={e => setRecurring(e.target.checked)} />Повторять</label>
        </> : <label className="block">Дата и время в часовом поясе устройства
          <input required type="datetime-local" value={at} onChange={e => setAt(e.target.value)} className="mt-1 rounded bg-ink-800 p-2" /></label>}
        <p className="text-neutral-500">Расписание привязано к текущему проекту и рабочей копии. Результат появится в этом чате. В режиме «С подтверждением» изменения ждут вашего решения.</p>
        <button disabled={change.isPending || !prompt.trim()} className="primary-button !py-1.5 text-xs">Создать расписание</button>
      </form>
      {(change.error || state.error) && <p role="alert" className="text-red-300">{change.error?.message || state.error?.message}</p>}
    </div>
  </details>;
}
