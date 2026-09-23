"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, MonitorPlay, MousePointerClick, Play, RotateCw, ScrollText, Square } from "lucide-react";
import { api, type PreviewState } from "@/lib/api";
import { pickMessage, type PickedElement } from "@/lib/pick";
import { PickBar, PickFrame } from "@/components/PickBar";

const STATES: Record<string, string> = {
  none: "не запущено",
  starting: "запускается…",
  running: "работает",
  no_port: "запущено, но не слушает порт",
  exited: "процесс завершился",
  stopped: "остановлено",
  restarted: "перезапущено",
  idle: "остановлено: долго не открывали",
  lifetime: "остановлено: слишком долго работало",
  unavailable: "песочница недоступна",
};
const LIVE = new Set(["starting", "running", "no_port"]);
// Приложение живёт на отдельном адресе; allow-same-origin относится к нему, а не к Лейле.
const FRAME_SANDBOX = "allow-scripts allow-forms allow-same-origin allow-popups allow-modals allow-downloads";

/**
 * Превью запущенного приложения проекта: dev-сервер (npm run dev, manage.py runserver…) или
 * статический сервер в песочнице, открытый в iframe. Клик по элементу — задача агенту в чат.
 */
export function PreviewPanel({ projectId, changed, onPicked }: {
  projectId: string;
  /** Счётчик правок файлов: статическому серверу нужно перезагрузить страницу. */
  changed: number;
  /** Правка по клику отправлена в чат. */
  onPicked?: () => void;
}) {
  const qc = useQueryClient();
  const key = ["preview", projectId];
  const status = useQuery({
    queryKey: key,
    queryFn: () => api.get<PreviewState>(`/projects/${projectId}/preview`),
    refetchInterval: query => {
      const state = query.state.data?.state;
      return state === "starting" ? 1500 : state && LIVE.has(state) ? 8000 : false;
    },
  });
  const data = status.data;
  const state = data?.state || "none";
  const [command, setCommand] = useState("");
  const [busy, setBusy] = useState<"start" | "stop" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [reload, setReload] = useState(0);
  const [picking, setPicking] = useState(false);
  const [pickedEl, setPickedEl] = useState<PickedElement | null>(null);
  const logs = useRef<HTMLPreElement>(null);
  const url = state === "running" ? data?.url : undefined;
  const staticServer = /http\.server|serve\b/.test(data?.command || "");

  useEffect(() => { if (data?.command && !command) setCommand(data.command); }, [data?.command]); // eslint-disable-line react-hooks/exhaustive-deps
  // Проблемы видны сразу: при сбое запуска открываем журнал.
  useEffect(() => { if (state === "exited" || state === "no_port") setShowLogs(true); }, [state]);
  useEffect(() => { if (showLogs && logs.current) logs.current.scrollTop = logs.current.scrollHeight; }, [showLogs, data?.logs]);
  // Dev-серверы обновляют страницу сами (HMR); статический — перезагружаем после правок.
  useEffect(() => {
    if (!changed || !staticServer) return;
    const timer = window.setTimeout(() => setReload(n => n + 1), 1500);
    return () => window.clearTimeout(timer);
  }, [changed, staticServer]);

  async function start() {
    setBusy("start"); setError(null); setPickedEl(null);
    try {
      const next = await api.post<PreviewState>(`/projects/${projectId}/preview`, { command: command.trim() || null });
      qc.setQueryData(key, next);
      setReload(n => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось запустить превью");
    } finally { setBusy(null); }
  }

  async function stop() {
    setBusy("stop"); setError(null);
    try {
      await api.del(`/projects/${projectId}/preview`);
      await qc.invalidateQueries({ queryKey: key });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось остановить превью");
    } finally { setBusy(null); setPicking(false); setPickedEl(null); }
  }

  function sendPick(request: string) {
    if (!pickedEl) return;
    const text = pickMessage(pickedEl, request);
    window.dispatchEvent(new CustomEvent("layla:chat-send", { detail: { domain: "code", project_id: projectId, text } }));
    setPicking(false); setPickedEl(null);
    onPicked?.();
  }

  const live = LIVE.has(state);
  const unavailable = state === "unavailable";
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-700/70 px-3 py-2">
        <MonitorPlay className="h-4 w-4 shrink-0 text-accent-300" />
        <input value={command} onChange={e => setCommand(e.target.value)} aria-label="Команда запуска"
          onKeyDown={e => { if (e.key === "Enter" && !busy && !unavailable) start(); }}
          placeholder={data?.suggested ? `Авто: ${data.suggested}` : "Команда: авто (npm run dev, manage.py, index.html…)"}
          className="min-w-0 flex-1 rounded-lg bg-ink-950 px-3 py-1.5 font-mono text-xs outline-none placeholder:font-sans" />
        <button onClick={start} disabled={!!busy || unavailable} className="primary-button h-8 !px-3 text-xs">
          {busy === "start" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : live ? <RotateCw className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          {live ? "Перезапустить" : "Запустить"}
        </button>
        {live && <button onClick={stop} disabled={!!busy} className="secondary-button h-8 !px-3 text-xs"><Square className="h-3.5 w-3.5" />Стоп</button>}
      </div>
      <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-xs text-neutral-400">
        <span className={`inline-flex items-center gap-1.5 ${state === "running" ? "text-emerald-300" : state === "exited" || unavailable ? "text-red-300" : ""}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${state === "running" ? "bg-emerald-400" : state === "starting" ? "animate-pulse bg-amber-300" : "bg-neutral-500"}`} />
          {STATES[state] || state}{data?.port && state === "running" ? ` · порт ${data.port}` : ""}{state === "exited" && data?.exit_code != null ? ` (код ${data.exit_code})` : ""}
        </span>
        <span className="ml-auto flex items-center gap-1">
          {url && <>
            <button onClick={() => { setPicking(p => !p); setPickedEl(null); }} aria-pressed={picking} className={`segment !h-7 !px-2 ${picking ? "!bg-accent-500/20 !text-accent-100" : ""}`}>
              <MousePointerClick className="h-3.5 w-3.5" />Правка по клику
            </button>
            <button onClick={() => setReload(n => n + 1)} className="icon-button !h-7 !w-7" aria-label="Обновить страницу"><RotateCw className="h-3.5 w-3.5" /></button>
            <a href={url} target="_blank" rel="noopener noreferrer" className="icon-button !h-7 !w-7" aria-label="Открыть в новой вкладке"><ExternalLink className="h-3.5 w-3.5" /></a>
          </>}
          {data && !unavailable && state !== "none" && (
            <button onClick={() => setShowLogs(v => !v)} aria-pressed={showLogs} className="icon-button !h-7 !w-7" aria-label="Журнал сервера"><ScrollText className="h-3.5 w-3.5" /></button>
          )}
        </span>
      </div>
      {error && <p role="alert" className="mx-3 mb-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
      <div className="relative min-h-0 flex-1 bg-white/[.02]">
        {url ? (
          <PickFrame key={`${url}#${reload}`} src={url} sandbox={FRAME_SANDBOX} title="Превью приложения"
            picking={picking} onPicked={element => { setPickedEl(element); setPicking(false); }}
            onCancel={() => { setPicking(false); setPickedEl(null); }} className="h-full w-full border-0 bg-white" />
        ) : (
          <div className="grid h-full place-items-center p-6 text-center text-sm text-neutral-400">
            {status.isLoading ? <Loader2 className="h-5 w-5 animate-spin" /> : unavailable ? (
              <div className="max-w-sm space-y-2">
                <p className="text-neutral-200">Песочница недоступна</p>
                <p className="text-xs">{data?.error || "Проверьте, что сервис sandbox запущен (docker compose ps)."}</p>
              </div>
            ) : state === "starting" ? (
              <div className="space-y-2"><Loader2 className="mx-auto h-6 w-6 animate-spin text-accent-300" /><p>Ставлю зависимости и запускаю сервер…</p></div>
            ) : (
              <div className="max-w-sm space-y-2">
                <MonitorPlay className="mx-auto h-8 w-8 text-accent-300" />
                <p className="text-neutral-200">Живое превью приложения</p>
                <p className="text-xs leading-5">Лейла запустит проект в песочнице (dev-сервер или статический сайт) и покажет его здесь. Изменения файлов подхватываются сами. Агент тоже может запустить превью — попросите в чате.</p>
              </div>
            )}
          </div>
        )}
      </div>
      <PickBar picking={picking} element={pickedEl} onSend={sendPick} onCancel={() => { setPicking(false); setPickedEl(null); }} />
      {showLogs && (
        <pre ref={logs} aria-label="Журнал сервера" className="max-h-56 shrink-0 overflow-auto border-t border-ink-700/70 bg-ink-950 px-3 py-2 font-mono text-[11px] leading-5 text-neutral-300 whitespace-pre-wrap">
          {data?.logs || "Журнал пуст"}
        </pre>
      )}
    </div>
  );
}
