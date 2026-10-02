"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type EngagementRecord, type EngagementWorkbench } from "@/lib/api";
import { useAuth } from "@/store/auth";

// Match the recorded vulnerability type, never keywords in prose or a title.
function criticalType(record: EngagementRecord): string | null {
  if (record.kind !== "finding" || record.status !== "confirmed" || record.data.severity !== "CRITICAL") return null;
  if (!Array.isArray(record.data.evidence_step_ids) || !record.data.evidence_step_ids.length || !record.data.control_step_id) return null;
  const type = String(record.data.type || "").trim().toUpperCase();
  if (["SQLI", "SQL INJECTION", "SQL-ИНЪЕКЦИЯ"].includes(type)) return "SQLi";
  if (["RCE", "REMOTE CODE EXECUTION", "УДАЛЁННОЕ ВЫПОЛНЕНИЕ КОДА"].includes(type)) return "RCE";
  return null;
}

export function CriticalFindings({ eid }: { eid: string }) {
  const owner = useAuth(s => s.user?.id);
  const { data, isError } = useQuery({ queryKey: ["pentest-workbench", owner, eid], enabled: !!owner,
    queryFn: () => api.get<EngagementWorkbench>(`/engagements/${eid}/workbench`), refetchInterval: 2000 });
  const findings = isError ? [] : (data?.records || []).filter(record => criticalType(record));
  if (!findings.length) return null;
  return <section aria-label="Подтверждённые критические находки" className="space-y-3">
    {findings.map(record => <article key={record.id} className="min-w-0 rounded-xl border border-red-400/50 bg-red-500/10 p-4">
      <p className="text-xs font-semibold text-red-300">CRITICAL · Подтверждено</p>
      <h3 className="mt-1 break-words text-2xl font-bold text-red-200 sm:text-3xl">{criticalType(record)}</h3>
      <p className="mt-2 break-words text-sm">{record.title}</p>
      {typeof record.data.url === "string" && <p className="mt-1 break-all text-xs text-neutral-400">{record.data.url}</p>}
      <details className="mt-2 text-xs"><summary className="cursor-pointer text-red-200">Основание подтверждения</summary>
        <p className="mt-2 whitespace-pre-wrap break-words">{String(record.data.observed || "")}</p>
        <p className="mt-2 break-words">Шаги: {(record.data.evidence_step_ids as string[]).join(", ")}</p>
        <p className="break-words">Контроль: {String(record.data.control_step_id)}</p>
      </details>
    </article>)}
  </section>;
}
