"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, CircleAlert, CircleCheck, Clock3, Cpu, Loader2, SquareTerminal } from "lucide-react";
import type { ToolEvent } from "@/lib/api";

/** Вывод команды: моноширинный, с автопрокруткой вниз, пока пользователь сам не прокрутил выше. */
export function OutputPane({ text, live, className = "max-h-72" }: { text: string; live: boolean; className?: string }) {
  const box = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [text]);
  return (
    <pre ref={box} onScroll={e => {
      const el = e.currentTarget;
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    }} className={`overflow-auto whitespace-pre-wrap break-words bg-ink-950/70 p-3 font-mono text-[11px] leading-relaxed text-neutral-300 ${className}`}>
      {text || (live ? "" : "Команда ничего не вывела.")}
      {live && <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-accent-300 align-middle" />}
    </pre>
  );
}

export function seconds(value?: number) {
  if (value === undefined || value === null) return "";
  return value >= 60 ? `${Math.floor(value / 60)} мин ${Math.round(value % 60)} с` : `${value < 10 ? value.toFixed(1) : Math.round(value)} с`;
}

/** Итог запуска одной строкой: код выхода, таймаут, ошибка. */
export function RunBadge({ tool }: { tool: ToolEvent }) {
  if (tool.status === "running") return <span className="flex items-center gap-1 text-accent-200"><Loader2 className="h-3 w-3 animate-spin" />Выполняется</span>;
  if (tool.status === "error") return <span className="flex items-center gap-1 text-red-300"><CircleAlert className="h-3 w-3" />Ошибка</span>;
  if (tool.timed_out) return <span className="flex items-center gap-1 text-amber-200"><Clock3 className="h-3 w-3" />Время вышло</span>;
  const ok = tool.exit_code === 0;
  return <span className={`flex items-center gap-1 ${ok ? "text-emerald-300" : "text-red-300"}`}>
    {ok ? <CircleCheck className="h-3 w-3" /> : <CircleAlert className="h-3 w-3" />}код {tool.exit_code ?? "?"}
  </span>;
}

/** Карточка run_command / run_code в чате. Успешный вывод свёрнут, упавший и живой — раскрыт. */
export function RunCard({ tool }: { tool: ToolEvent }) {
  const live = tool.status === "running";
  const failed = tool.status === "error" || !!tool.timed_out || (tool.status === "done" && tool.exit_code !== 0);
  const [open, setOpen] = useState<boolean | null>(null);
  const shown = open ?? (live || failed);
  const Icon = tool.name === "run_code" ? Cpu : SquareTerminal;
  const output = tool.output || "";
  return (
    <div className={`overflow-hidden rounded-lg border text-xs ${failed ? "border-red-500/30" : "border-ink-700"}`}>
      <button onClick={() => setOpen(!shown)} aria-expanded={shown} className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-ink-800/60">
        <Icon className="h-3.5 w-3.5 shrink-0 text-accent-300" />
        <code className="min-w-0 flex-1 truncate font-mono text-neutral-200" title={tool.command}>{tool.name === "run_command" ? "$ " : ""}{tool.command || tool.name}</code>
        {tool.duration_s !== undefined && !live && <span className="shrink-0 text-neutral-500">{seconds(tool.duration_s)}</span>}
        <span className="shrink-0"><RunBadge tool={tool} /></span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-neutral-500 transition-transform ${shown ? "rotate-180" : ""}`} />
      </button>
      {shown && <div className="border-t border-ink-700">
        {live && tool.note && !output && <p className="px-3 py-2 text-neutral-500">{tool.note}</p>}
        {(output || !live) && <OutputPane text={output} live={live} />}
        {tool.error && <p className="border-t border-red-500/20 px-3 py-2 text-red-300">{tool.error}</p>}
      </div>}
    </div>
  );
}
