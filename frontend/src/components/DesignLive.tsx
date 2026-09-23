"use client";

import { useEffect, useRef, useState } from "react";
import { Brain, ChevronDown, CircleStop, Loader2, X } from "lucide-react";
import type { Job } from "@/lib/api";

export const isActive = (job?: Job | null) => !!job && (job.status === "queued" || job.status === "running");

/** HTML из черновика: содержимое блока ```html, даже если он ещё не закрыт. */
export function draftHtml(text: string): string {
  const open = /```[ \t]*(?:html|htm)?[ \t]*\n?/i.exec(text);
  if (!open) return /^\s*</.test(text) ? text : "";
  const rest = text.slice(open.index + open[0].length);
  const close = rest.indexOf("```");
  return close >= 0 ? rest.slice(0, close) : rest;
}

// Во время генерации превью само прокручивается к последней написанной секции.
const FOLLOW = "<script>(function(){function go(){try{scrollTo(0,document.documentElement.scrollHeight)}catch(e){}}"
  + "addEventListener('DOMContentLoaded',go);addEventListener('load',go);setTimeout(go,400)})()</script>";

function withFollow(html: string) {
  const head = /<head[^>]*>/i.exec(html);
  return head ? html.slice(0, head.index + head[0].length) + FOLLOW + html.slice(head.index + head[0].length) : html;
}

/** Значение не чаще, чем раз в ms: iframe не перезагружается на каждый токен. */
function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  const last = useRef(0);
  useEffect(() => {
    const wait = Math.max(0, last.current + ms - Date.now());
    const timer = setTimeout(() => { last.current = Date.now(); setShown(value); }, wait);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return shown;
}

/**
 * Два iframe по очереди: новый вариант грузится в скрытый и показывается, только когда
 * загрузился. Так превью не мигает белым при каждом обновлении.
 */
export function LiveFrame({ html }: { html: string }) {
  const [docs, setDocs] = useState<[string, string]>([html, ""]);
  const [front, setFront] = useState(0);
  const frontRef = useRef(0);
  const loading = useRef<number | null>(null);
  const shown = useRef(html);   // что сейчас на виду
  const target = useRef(html);  // что грузится в скрытый iframe
  const latest = useRef(html);  // самое свежее, что пришло
  const seq = useRef(0);

  function load(doc: string) {
    const back = 1 - frontRef.current;
    loading.current = back;
    target.current = doc;
    // Метка делает srcDoc уникальным: одинаковый srcDoc iframe не перезагружает, и onLoad не придёт.
    const stamped = `${doc}<!--${++seq.current}-->`;
    setDocs(prev => (back === 0 ? [stamped, prev[1]] : [prev[0], stamped]));
  }

  useEffect(() => {
    latest.current = html;
    if (loading.current === null && shown.current !== html) load(html);
  }, [html]);

  function loaded(index: number) {
    if (loading.current !== index) return;
    loading.current = null;
    frontRef.current = index;
    shown.current = target.current;
    setFront(index);
    if (latest.current !== shown.current) load(latest.current);
  }

  return (
    <div className="relative h-full w-full bg-white">
      {docs.map((doc, i) => (
        <iframe key={i} title={i === front ? "preview" : "preview-next"} sandbox="allow-scripts" srcDoc={doc}
          onLoad={() => loaded(i)} aria-hidden={i !== front}
          className={`absolute inset-0 h-full w-full border-0 ${i === front ? "z-10 opacity-100" : "pointer-events-none z-0 opacity-0"}`} />
      ))}
    </div>
  );
}

/** Код с автопрокруткой вниз, пока пользователь сам не прокрутил выше. */
export function CodeStream({ code, live }: { code: string; live: boolean }) {
  const box = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && live && stick.current) el.scrollTop = el.scrollHeight;
  }, [code, live]);
  return (
    <pre ref={box} onScroll={e => {
      const el = e.currentTarget;
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    }} className="h-full overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-[11px] leading-relaxed text-neutral-300">
      <code>{code}</code>{live && <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-accent-300 align-middle" />}
    </pre>
  );
}

function Reasoning({ text, live, writing }: { text: string; live: boolean; writing: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(true);
  useEffect(() => {
    if (box.current && live) box.current.scrollTop = box.current.scrollHeight;
  }, [text, live, open]);
  // На телефоне места мало: когда модель начала писать код, размышления сворачиваются (можно раскрыть).
  useEffect(() => {
    if (writing && window.matchMedia?.("(max-width: 760px)").matches) setOpen(false);
  }, [writing]);
  return (
    <div className="shrink-0 border-b border-ink-700/60">
      <button onClick={() => setOpen(v => !v)} aria-expanded={open} className="flex w-full items-center gap-2 px-4 py-2 text-xs text-neutral-300">
        <Brain className={`h-3.5 w-3.5 text-accent-300 ${live ? "animate-pulse" : ""}`} />
        <span className="flex-1 text-left">{live ? "Модель думает…" : "Размышления модели"}</span>
        <span className="text-[11px] text-neutral-500">{Math.round(text.length / 100) / 10}K симв.</span>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div ref={box} className="max-h-40 overflow-y-auto whitespace-pre-wrap px-4 pb-3 text-[11px] leading-5 text-neutral-400">
          {text}
        </div>
      )}
    </div>
  );
}

function elapsed(from?: string, now = Date.now()) {
  if (!from) return "";
  const start = new Date(/[Z+]/.test(from.slice(10)) ? from : from + "Z").getTime();
  const s = Math.max(0, Math.round((now - start) / 1000));
  return s >= 60 ? `${Math.floor(s / 60)} мин ${s % 60} с` : `${s} с`;
}

type Props = {
  job: Job;
  view: "preview" | "code" | "split";
  width: string;
  onStop: () => void;
  onClose: () => void;
  stopping: boolean;
};

/** Генерация по брифу в реальном времени: размышления, код и превью по ходу записи. */
export function DesignLive({ job, view, width, onStop, onClose, stopping }: Props) {
  const active = isActive(job);
  const draft = typeof job.result.draft === "string" ? job.result.draft : "";
  const html = draftHtml(draft);
  const preview = useThrottled(active ? withFollow(html) : html, 1200);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);

  const model = typeof job.result.model === "string" ? job.result.model : "";
  const step = job.status === "cancelled" ? "Генерация остановлена"
    : job.error || job.steps.at(-1)?.text || "Ожидает начала";
  const lines = draft ? draft.split("\n").length : 0;
  const rendered = /<(body|header|main|section|nav|div|h1)\b/i.test(html);
  const failed = job.status === "error" || job.status === "cancelled";

  const code = <CodeStream code={draft || (active ? "" : "Модель не успела написать код.")} live={active} />;
  const frame = (
    <div className="relative mx-auto h-full" style={{ width, maxWidth: "100%" }}>
      {rendered ? <LiveFrame html={preview} /> : (
        <div className="grid h-full place-items-center rounded bg-ink-900/60 p-6 text-center text-xs text-neutral-500">
          {active ? (draft ? "Модель пишет стили — превью появится с первой секцией" : "Ждём первые строки кода…") : "Превью нет"}
        </div>
      )}
    </div>
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-ink-700/60 px-4 py-2.5">
        <div className="flex items-center gap-2 text-xs">
          {active ? <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent-300" /> : <span className={`h-2 w-2 shrink-0 rounded-full ${failed ? "bg-red-400" : "bg-emerald-400"}`} />}
          <span className={`min-w-0 flex-1 truncate ${failed ? "text-red-300" : "text-neutral-200"}`} title={step}>{step}</span>
          {active ? (
            <button onClick={onStop} disabled={stopping} className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-neutral-400 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50">
              <CircleStop className="h-3.5 w-3.5" />{stopping ? "Останавливаю…" : "Остановить"}
            </button>
          ) : (
            <button onClick={onClose} aria-label="Закрыть просмотр генерации" className="shrink-0 rounded-md p-1 text-neutral-400 hover:bg-ink-800 hover:text-white">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-ink-800">
          <div className={`h-full rounded-full transition-all duration-500 ${failed ? "bg-red-400/70" : "bg-accent-400"}`} style={{ width: `${Math.round((job.progress || 0) * 100)}%` }} />
        </div>
        <p className="mt-1.5 flex flex-wrap gap-x-3 text-[11px] text-neutral-500">
          {model && <span>{model}</span>}
          <span>{elapsed(job.created_at, active ? now : new Date(job.updated_at || Date.now()).getTime())}</span>
          <span>{lines} строк · {Math.round(draft.length / 102.4) / 10} КБ</span>
          {failed && draft && <span className="text-amber-300">Черновик не сохранён как версия</span>}
        </p>
      </div>
      {job.reasoning && <Reasoning text={job.reasoning} live={active && !draft} writing={!!draft} />}
      <div className="min-h-0 flex-1">
        {view === "code" ? code : view === "preview" ? <div className="h-full p-3">{frame}</div> : (
          <div className="grid h-full min-h-0 grid-rows-2 lg:grid-cols-2 lg:grid-rows-1">
            <div className="min-h-0 border-b border-ink-700/60 lg:border-b-0 lg:border-r">{code}</div>
            <div className="min-h-0 p-3">{frame}</div>
          </div>
        )}
      </div>
    </div>
  );
}
