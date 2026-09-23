"use client";

import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { api } from "@/lib/api";

interface AuditEntry {
  id: string;
  action: string;
  target?: string | null;
  note?: string | null;
  ts: string;
}

export function SecuritySettings() {
  const { data: entries = [] } = useQuery({
    queryKey: ["audit"],
    queryFn: () => api.get<AuditEntry[]>("/audit?limit=200"),
  });

  return (
    <div>
      <h1 className="text-xl font-semibold">Безопасность</h1>
      <p className="mb-4 text-sm text-neutral-500">
        Аудит-лог действий (смены scope, авторизация, шаги агента, импорт и т.д.).
        Обходов scope/авторизации в продукте нет (спец. §7).
      </p>

      <a
        href="/api/audit/export.csv"
        className="mb-4 inline-flex items-center gap-1.5 rounded-md bg-ink-700 px-3 py-1.5 text-xs text-white hover:bg-ink-600"
      >
        <Download className="h-3.5 w-3.5" /> Экспорт аудита (CSV)
      </a>

      {entries.length === 0 ? (
        <p className="text-xs text-neutral-600">Записей пока нет.</p>
      ) : (
        <ul className="divide-y divide-ink-700 rounded-lg border border-ink-700 text-xs">
          {entries.map((e) => (
            <li key={e.id} className="flex items-center gap-2 px-3 py-2">
              <span className="font-mono text-[11px] text-neutral-600">
                {new Date(e.ts).toLocaleString()}
              </span>
              <span className="rounded bg-ink-700 px-1.5 py-0.5 text-[11px] text-neutral-300">
                {e.action}
              </span>
              <span className="truncate text-neutral-500">{e.target || ""}</span>
              {e.note && <span className="ml-auto truncate text-neutral-600">{e.note}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
