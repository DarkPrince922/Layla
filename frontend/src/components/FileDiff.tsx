"use client";

import { ChevronDown, FileCode2, ArrowUpRight } from "lucide-react";
import type { FileChange } from "@/lib/api";

const labels = { create: "Создан файл", edit: "Изменён файл", delete: "Удалён файл" };

export function FileDiff({ change, expanded = false, onOpen }: {
  change: FileChange;
  expanded?: boolean;
  onOpen?: (path: string) => void;
}) {
  const lines = change.diff?.split("\n") || [];
  const added = lines.filter(line => line.startsWith("+") && !line.startsWith("+++")).length;
  const removed = lines.filter(line => line.startsWith("-") && !line.startsWith("---")).length;
  return <details open={expanded} className="group my-2 min-w-0 overflow-hidden rounded-xl border border-ink-600/60 bg-ink-800/25">
    <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3.5 [&::-webkit-details-marker]:hidden">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent-500/15 text-accent-300"><FileCode2 className="h-4 w-4" /></span>
      <span className="min-w-0 flex-1"><span className={`block text-xs font-medium ${change.operation === "delete" ? "text-red-300" : "text-neutral-200"}`}>{labels[change.operation]}</span><span className="mt-0.5 block break-all font-mono text-[11px] text-neutral-400">{change.path}</span></span>
      <span className="flex shrink-0 gap-2 font-mono text-xs">{added > 0 && <span className="text-emerald-300">+{added}</span>}{removed > 0 && <span className="text-red-300">−{removed}</span>}</span>
      <ChevronDown className="h-4 w-4 shrink-0 text-neutral-500 transition-transform group-open:rotate-180" />
    </summary>
    <div className="border-t border-ink-700/70">
      <pre className="max-h-80 overflow-auto py-3 text-xs leading-6">{change.diff ? lines.map((line, i) => <div key={i} className={`min-w-max px-4 ${line.startsWith("+") ? "bg-emerald-500/10 text-emerald-300" : line.startsWith("-") ? "bg-red-500/10 text-red-300" : line.startsWith("@@") ? "text-accent-300" : "text-neutral-400"}`}>{line || " "}</div>) : <span className="px-4 text-neutral-500">Содержимое файла не изменилось.</span>}</pre>
      {onOpen && change.operation !== "delete" && <div className="border-t border-ink-700/60 px-3 py-2"><button type="button" onClick={() => onOpen(change.path)} className="flex items-center gap-2 rounded-full px-3 py-2 text-xs text-accent-200 hover:bg-accent-500/10">Открыть в редакторе<ArrowUpRight className="h-3.5 w-3.5" /></button></div>}
    </div>
  </details>;
}
