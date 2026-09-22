"use client";

import { useState } from "react";
import { ChevronDown, FilePlus2, FilePen, FileX2 } from "lucide-react";
import type { FileChange } from "@/lib/api";

const meta = {
  create: { label: "Создан", icon: FilePlus2, accent: "text-emerald-400", bar: "bg-emerald-500" },
  edit: { label: "Изменён", icon: FilePen, accent: "text-indigo-300", bar: "bg-indigo-500" },
  delete: { label: "Удалён", icon: FileX2, accent: "text-red-400", bar: "bg-red-500" },
} as const;

// Каждое файловое изменение агента — отдельная карточка: шапка со значком
// операции, путём и счётчиком +/− строк, тело с раскрывающимся диффом.
export function FileDiff({
  change,
  expanded = false,
}: {
  change: FileChange;
  expanded?: boolean;
}) {
  const [open, setOpen] = useState(expanded);
  const m = meta[change.operation];
  const Icon = m.icon;
  const lines = change.diff ? change.diff.split("\n") : [];
  const added = lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
  const removed = lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length;
  const name = change.path.split("/").pop() || change.path;
  const dir = change.path.slice(0, change.path.length - name.length);

  return (
    <div className="my-2 min-w-0 overflow-hidden rounded-lg border border-ink-700 bg-ink-950">
      <div className="flex items-stretch">
        <span className={`w-1 shrink-0 ${m.bar}`} aria-hidden />
        <button
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2 text-left hover:bg-ink-900"
        >
          <Icon className={`h-4 w-4 shrink-0 ${m.accent}`} />
          <span className="min-w-0 flex-1 truncate text-xs">
            {dir && <span className="text-neutral-500">{dir}</span>}
            <span className="font-medium text-neutral-200">{name}</span>
          </span>
          <span className={`shrink-0 text-[10px] font-semibold uppercase tracking-wide ${m.accent}`}>
            {m.label}
          </span>
          {(added > 0 || removed > 0) && (
            <span className="shrink-0 font-mono text-[10px]">
              {added > 0 && <span className="text-emerald-400">+{added}</span>}
              {added > 0 && removed > 0 && " "}
              {removed > 0 && <span className="text-red-400">−{removed}</span>}
            </span>
          )}
          <ChevronDown
            className={`h-3.5 w-3.5 shrink-0 text-neutral-500 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </button>
      </div>
      {open && (
        <pre className="max-h-80 overflow-auto border-t border-ink-700 py-2 text-[11px] leading-5">
          {change.diff ? (
            lines.map((line, i) => (
              <div
                key={i}
                className={`min-w-max px-3 ${
                  line.startsWith("+") && !line.startsWith("+++")
                    ? "bg-emerald-500/10 text-emerald-300"
                    : line.startsWith("-") && !line.startsWith("---")
                      ? "bg-red-500/10 text-red-300"
                      : line.startsWith("@@")
                        ? "text-indigo-300"
                        : "text-neutral-400"
                }`}
              >
                {line || " "}
              </div>
            ))
          ) : (
            <span className="px-3 text-neutral-500">
              Изменений в строках нет (пустой файл или прежнее содержимое).
            </span>
          )}
        </pre>
      )}
    </div>
  );
}
