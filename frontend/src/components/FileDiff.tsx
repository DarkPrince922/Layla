"use client";

import type { FileChange } from "@/lib/api";

const labels = { create: "Создан", edit: "Изменён", delete: "Удалён" };

export function FileDiff({
  change,
  expanded = false,
}: {
  change: FileChange;
  expanded?: boolean;
}) {
  return (
    <details
      open={expanded}
      className="my-2 min-w-0 overflow-hidden rounded-lg border border-ink-700 bg-ink-950"
    >
      <summary className="cursor-pointer break-all px-3 py-2 text-xs text-neutral-300">
        <span
          className={
            change.operation === "delete" ? "text-red-400" : "text-emerald-400"
          }
        >
          {labels[change.operation]}
        </span>{" "}
        {change.path}
      </summary>
      <pre className="max-h-80 overflow-auto border-t border-ink-700 py-2 text-[11px] leading-5">
        {change.diff ? (
          change.diff.split("\n").map((line, i) => (
            <div
              key={i}
              className={`min-w-max px-3 ${line.startsWith("+") ? "bg-emerald-500/10 text-emerald-300" : line.startsWith("-") ? "bg-red-500/10 text-red-300" : line.startsWith("@@") ? "text-indigo-300" : "text-neutral-400"}`}
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
    </details>
  );
}
