"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Loader2, MessageSquare } from "lucide-react";
import { api } from "@/lib/api";
import { confirmAction } from "@/components/ConfirmDialog";

type TeamState = {
  team: { id: string; name: string; description: string; active: boolean;
    members: { id: string; name: string; role: string; status: string; workspace_removed: boolean }[] } | null;
  teams: { id: string; name: string; active: boolean }[];
  messages: { id: string; from: string; to: string; summary: string; message: string; delivered: boolean }[];
  before?: number | null;
  requests?: { id: string; kind: "plan" | "shutdown"; from: string; to: string; content: string; status: string; response: string }[];
};

export function CodeTeamPanel({ chatId, scope, running, canApply }: {
  chatId: string; scope: string; running: boolean; canApply: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState("");
  const [before, setBefore] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [to, setTo] = useState("*");
  const [message, setMessage] = useState("");
  const [shutdownFor, setShutdownFor] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const state = useQuery({
    queryKey: ["code-team", scope, chatId, selected, before],
    queryFn: () => { const params = new URLSearchParams(); if (selected) params.set("team_id", selected); if (before != null) params.set("before", String(before)); return api.get<TeamState>(`/chats/${chatId}/team?${params}`); },
    enabled: open,
    refetchInterval: query => open && (running || query.state.data?.team?.members.some(m => ["queued", "running"].includes(m.status))) ? 1500 : false,
    retry: false,
  });
  const team = state.data?.team;
  const active = team?.members.filter(m => ["queued", "running"].includes(m.status)).length || 0;
  async function action(kind: "create" | "message" | "close") {
    if (busy) return;
    if (kind === "close" && !(await confirmAction("Закрыть команду? Переписка, отчёты и рабочие копии сохранятся."))) return;
    setBusy(true); setError(null);
    try {
      await api.post(`/chats/${chatId}/team/${kind}`, kind === "create" ? { team_name: name } : kind === "message" ? { to, message, summary: message.slice(0, 120) } : {});
      if (kind === "message") setMessage("");
      else { setSelected(""); setName(""); }
      setBefore(null);
      await state.refetch();
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось изменить команду"); }
    finally { setBusy(false); }
  }
  async function protocol(action: "request" | "respond", body: object) {
    setBusy(true); setError(null);
    try {
      await api.post(`/chats/${chatId}/team/protocol/${action}`, body);
      setShutdownFor(""); await state.refetch();
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось обновить запрос команды"); }
    finally { setBusy(false); }
  }
  return <details onToggle={e => setOpen(e.currentTarget.open)} className="rounded-xl border border-ink-700/70 bg-ink-950/20 p-3 text-xs">
    <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
      {active ? <Loader2 className="h-4 w-4 animate-spin text-accent-300" /> : <MessageSquare className="h-4 w-4 text-accent-300" />}
      <span className="flex-1 text-neutral-300">Команда{team ? ` · ${team.name}` : " и сообщения"}{active ? ` · работают ${active}` : ""}</span>
      <ChevronDown className="h-3.5 w-3.5 text-neutral-500" />
    </summary>
    <div className="mt-3 space-y-3 border-t border-ink-700 pt-3">
      {state.isPending && <p className="text-neutral-500">Загружаю команду…</p>}
      {state.isError && <p role="alert" className="text-red-300">Не удалось загрузить команду</p>}
      {!!state.data?.teams.length && <select aria-label="Команда или архив" value={selected} onChange={e => { setSelected(e.target.value); setBefore(null); }} className="w-full rounded-lg bg-ink-900 p-2">
        <option value="">Текущая команда</option>
        {state.data.teams.map(item => <option key={item.id} value={item.id}>{item.name}{item.active ? " · активна" : " · архив"}</option>)}
      </select>}
      {!team && state.isSuccess && <div className="space-y-2">
        <p className="text-neutral-500">Ведущий может создать команду и назначить именованных воркеров. Их сообщения и история сохраняются в этом чате.</p>
        <div className="flex gap-2"><input aria-label="Имя команды" value={name} maxLength={60} onChange={e => setName(e.target.value)} placeholder="my-team" className="min-w-0 flex-1 rounded-lg bg-ink-900 p-2" />
          <button disabled={busy || !canApply || !/^[a-zA-Z][a-zA-Z0-9_-]{0,59}$/.test(name)} onClick={() => void action("create")} className="secondary-button !px-3 !py-1.5">Создать</button></div>
      </div>}
      {team && <>
        {team.description && <p className="text-neutral-400">{team.description}</p>}
        <div className="flex flex-wrap gap-2">{team.members.map(member => <span key={member.id} className="rounded-full border border-ink-700 px-2 py-1 text-neutral-400">{member.name} · {member.role} · {member.status}</span>)}</div>
        <p className="text-neutral-500">Сообщения поступают агентам между ходами. Для завершённого участника отправьте уточнение, затем нажмите «Продолжить» в его карточке.</p>
        {!!state.data?.requests?.length && <details className="rounded-lg border border-ink-700 p-2"><summary className="cursor-pointer">Планы и запросы на завершение · {state.data.requests.length}</summary>
          <div className="mt-2 space-y-2">{state.data.requests.map(item => <div key={item.id} className="rounded-lg bg-ink-900 p-2">
            <p>{item.kind === "plan" ? "План" : "Завершение"} · {item.from} → {item.to} · {item.status}</p><p className="mt-1 whitespace-pre-wrap break-words text-neutral-400">{item.content}</p>
            {item.status === "pending" && item.to === "lead" && <div className="mt-2 flex gap-2"><button disabled={busy || !canApply} onClick={() => void protocol("respond", { request_id: item.id, approve: true })} className="secondary-button !px-2 !py-1">Одобрить</button>
              <button disabled={busy || !canApply} onClick={() => void protocol("respond", { request_id: item.id, approve: false })} className="secondary-button !px-2 !py-1">Отклонить</button></div>}
          </div>)}</div></details>}
        {team.active && !!team.members.length && <div className="flex gap-2"><select aria-label="Участник для завершения" value={shutdownFor} onChange={e => setShutdownFor(e.target.value)} className="min-w-0 flex-1 rounded-lg bg-ink-900 p-2"><option value="">Выберите участника</option>{team.members.map(m => <option key={m.id} value={m.name}>{m.name}</option>)}</select>
          <button disabled={busy || !shutdownFor || !canApply} onClick={() => void protocol("request", { kind: "shutdown", to: shutdownFor, content: "Заверши текущую операцию и сохрани результат." })} className="secondary-button !px-2 !py-1">Запросить завершение</button></div>}
        {team.active && !!team.members.length && <form onSubmit={e => { e.preventDefault(); void action("message"); }} className="space-y-2">
          <select aria-label="Получатель сообщения" value={to} onChange={e => setTo(e.target.value)} className="w-full rounded-lg bg-ink-900 p-2">
            <option value="*">Всем участникам</option>
            {team.members.filter(m => !m.workspace_removed).map(m => <option key={m.id} value={m.name}>{m.name}</option>)}
          </select>
          <textarea aria-label="Уточнение команде" value={message} maxLength={12000} onChange={e => setMessage(e.target.value)} placeholder="Уточнить задачу или передать результат…" rows={2} className="w-full rounded-lg bg-ink-900 p-2" />
          <button disabled={busy || !canApply || !message.trim()} className="primary-button !px-3 !py-1.5 text-xs">Отправить команде</button>
        </form>}
        <details className="rounded-lg border border-ink-700 p-2">
          <summary className="cursor-pointer text-neutral-400">Переписка · последние {state.data?.messages.length || 0}</summary>
          <div className="mt-2 max-h-80 space-y-2 overflow-auto">{state.data?.messages.map(item => <details key={item.id} className="rounded-lg bg-ink-900 p-2">
            <summary className="cursor-pointer text-neutral-400">{item.from} → {item.to} · {item.summary || "Сообщение"} · {item.delivered ? "доставлено в контекст" : "ожидает хода"}</summary>
            <p className="mt-2 whitespace-pre-wrap break-words text-neutral-300">{item.message}</p>
          </details>)}</div>
          <div className="mt-2 flex gap-2">
            <button disabled={state.data?.before == null} onClick={() => setBefore(state.data?.before ?? null)} className="secondary-button !px-2 !py-1">Ранее</button>
            <button disabled={before == null} onClick={() => setBefore(null)} className="secondary-button !px-2 !py-1">К новым</button>
          </div>
        </details>
        {team.active ? <button disabled={busy || !canApply || !!active} onClick={() => void action("close")} className="secondary-button !px-3 !py-1.5 text-xs">Закрыть команду</button> : <p className="text-neutral-500">Команда закрыта. Переписка и результаты сохранены.</p>}
      </>}
      {error && <p role="alert" className="text-red-300">{error}</p>}
    </div>
  </details>;
}
