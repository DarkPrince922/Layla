"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Braces, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/store/auth";

const operations = [
  ["diagnostics", "Ошибки и предупреждения"], ["goToDefinition", "Определение"],
  ["findReferences", "Ссылки"], ["hover", "Тип и документация"],
  ["documentSymbol", "Символы файла"], ["workspaceSymbol", "Символы проекта"],
  ["goToImplementation", "Реализации"], ["prepareCallHierarchy", "Иерархия вызовов"],
  ["incomingCalls", "Кто вызывает"], ["outgoingCalls", "Что вызывает"],
];
const supported = /\.(pyi?|[cm]?[jt]sx?)$/i;
type Position = { line: number; character: number };
type Result = { operation: string; path: string; result: unknown; unsaved: boolean; stale_source: boolean };
type Row = { path: string; position: Position; label: string; severity?: number };

function locations(value: unknown, path: string, rows: Row[] = [], depth = 0): Row[] {
  if (depth > 20 || rows.length >= 100 || value === null || typeof value !== "object") return rows;
  if (Array.isArray(value)) { value.forEach(item => locations(item, path, rows, depth + 1)); return rows; }
  const item = value as Record<string, unknown>;
  const file = typeof item.path === "string" ? item.path : path;
  const range = (item.selectionRange || item.targetSelectionRange || item.range || item.targetRange) as { start?: Position } | undefined;
  if (range?.start && Number.isInteger(range.start.line) && Number.isInteger(range.start.character)) {
    rows.push({ path: file, position: range.start, label: String(item.name || item.message || "Перейти"),
      severity: typeof item.severity === "number" ? item.severity : undefined });
  }
  for (const [key, entry] of Object.entries(item)) {
    if (!["range", "selectionRange", "targetRange", "targetSelectionRange"].includes(key)) locations(entry, file, rows, depth + 1);
  }
  return rows;
}

export function CodeIntelligencePanel({ projectId, path, content, saved, position, onNavigate }: {
  projectId: string; path: string; content: string; saved: boolean; position: Position;
  onNavigate: (path: string, line: number, character: number) => void;
}) {
  const owner = useAuth(state => state.user?.id);
  const [open, setOpen] = useState(false);
  const [operation, setOperation] = useState("diagnostics");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const sequence = useRef(0);
  const status = useQuery({ queryKey: ["lsp-status", owner, projectId], enabled: open,
    queryFn: () => api.get<{ available: Record<string, { extensions: string[] }> }>(`/projects/${projectId}/lsp`), retry: false });
  useEffect(() => { sequence.current++; setResult(null); setError(null); setBusy(false); }, [projectId, path, content]);
  const available = Object.keys(status.data?.available || {}).length > 0;
  async function inspect() {
    const seq = ++sequence.current;
    setBusy(true); setError(null); setResult(null);
    try {
      const data = await api.post<Result>(`/projects/${projectId}/lsp`, {
        operation, path, content, line: position.line, character: position.character, query,
      });
      if (sequence.current === seq) setResult(data);
    } catch (e) { if (sequence.current === seq) setError(e instanceof Error ? e.message : "LSP не ответил"); }
    finally { if (sequence.current === seq) setBusy(false); }
  }
  const rows = result ? locations(result.result, path) : [];
  const diagnostics = result?.operation === "diagnostics" ? result.result as { ready?: boolean; items?: unknown[] } : null;
  return <details onToggle={event => setOpen(event.currentTarget.open)} className="shrink-0 border-t border-ink-700/60 bg-ink-900/40 text-xs">
    <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 text-neutral-400"><Braces className="h-4 w-4 text-accent-300" />Анализ кода · LSP</summary>
    <div className="space-y-3 px-4 pb-4">
      {status.isPending && open && <p className="text-neutral-500">Проверяю language servers…</p>}
      {(status.isError || status.data && !available) && <p className="text-neutral-500">Language servers недоступны. Запустите обновлённую песочницу.</p>}
      {!supported.test(path) && <p className="text-neutral-500">Сейчас поддерживаются Python и JavaScript/TypeScript.</p>}
      {!saved && <p className="text-neutral-500">Сначала сохраните новый файл.</p>}
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="Операция анализа кода" value={operation} onChange={event => setOperation(event.target.value)} className="rounded-lg border border-ink-700 bg-ink-900 px-2 py-2">
          {operations.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        {operation === "workspaceSymbol" && <input aria-label="Поиск символов" placeholder="Имя символа" value={query} onChange={event => setQuery(event.target.value)} className="min-w-0 rounded-lg border border-ink-700 bg-ink-900 px-2 py-2" />}
        <button disabled={busy || !available || !saved || !supported.test(path)} onClick={() => void inspect()} className="secondary-button !px-3 !py-2 text-xs">
          {busy && <Loader2 className="h-3 w-3 animate-spin" />}Проверить
        </button>
        <span className="text-neutral-500">Строка {position.line}, столбец {position.character}</span>
      </div>
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {result?.unsaved && <p className="text-amber-300">Проверен текущий черновик; файл на диске не менялся.</p>}
      {result?.stale_source && <p className="text-amber-300">Файл изменился во время анализа. Повторите проверку.</p>}
      {diagnostics && <p className={diagnostics.ready ? "text-neutral-400" : "text-amber-300"}>{!diagnostics.ready ? "Анализ ещё не готов. Повторите проверку." : diagnostics.items?.length ? `Диагностик: ${diagnostics.items.length}` : "Сервер не сообщил об ошибках в этом файле."}</p>}
      {!!rows.length && <div className="max-h-52 space-y-1 overflow-auto">{rows.map((row, index) => <button key={index} onClick={() => onNavigate(row.path, row.position.line + 1, row.position.character + 1)} className={`block w-full rounded-lg px-2 py-1.5 text-left hover:bg-ink-700/40 ${row.severity === 1 ? "text-red-300" : row.severity === 2 ? "text-amber-300" : "text-neutral-300"}`}>
        <span className="mr-2 text-neutral-500">{row.path}:{row.position.line + 1}:{row.position.character + 1}</span>{row.label}
      </button>)}</div>}
      {result && !diagnostics && <details><summary className="cursor-pointer text-neutral-500">Ответ сервера</summary><pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words text-neutral-400">{JSON.stringify(result.result, null, 2)}</pre></details>}
    </div>
  </details>;
}
