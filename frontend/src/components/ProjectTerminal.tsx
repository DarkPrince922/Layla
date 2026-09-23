"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Globe, Loader2, Play, RotateCcw, Square, SquareTerminal, Trash2 } from "lucide-react";
import {
  ApiError,
  api,
  runInProject,
  type FileNode,
  type RunCodeResult,
  type SandboxStatus,
  type ToolEvent,
} from "@/lib/api";
import { OutputPane, RunBadge, seconds } from "@/components/RunOutput";
import { confirmAction } from "@/components/ConfirmDialog";

/** Запуск, заказанный извне (кнопка «Запустить» у файла). */
export type RunRequest = { id: number; path: string };

type Entry = ToolEvent & { started: number };

const TIMEOUTS = [30, 120, 300, 600, 900];
const quote = (path: string) => `'${path.replace(/'/g, `'\\''`)}'`;

// Файл -> команда в песочнице (если там есть нужный инструмент).
const SANDBOX_RUN: Record<string, { tool: string; command: (p: string) => string }> = {
  py: { tool: "python", command: p => `python ${quote(p)}` },
  js: { tool: "node", command: p => `node ${quote(p)}` },
  mjs: { tool: "node", command: p => `node ${quote(p)}` },
  cjs: { tool: "node", command: p => `node ${quote(p)}` },
  ts: { tool: "node", command: p => `npx --yes tsx ${quote(p)}` },
  sh: { tool: "", command: p => `bash ${quote(p)}` },
  c: { tool: "gcc", command: p => `gcc -O2 ${quote(p)} -o "$TMPDIR/a.out" -lm && "$TMPDIR/a.out"` },
  cpp: { tool: "g++", command: p => `g++ -O2 -std=c++17 ${quote(p)} -o "$TMPDIR/a.out" && "$TMPDIR/a.out"` },
  cc: { tool: "g++", command: p => `g++ -O2 -std=c++17 ${quote(p)} -o "$TMPDIR/a.out" && "$TMPDIR/a.out"` },
  go: { tool: "go", command: p => `go run ${quote(p)}` },
  rs: { tool: "rustc", command: p => `rustc -O ${quote(p)} -o "$TMPDIR/a.out" && "$TMPDIR/a.out"` },
  java: { tool: "java", command: p => `java ${quote(p)}` },
};
// Файл -> язык Piston, если в песочнице инструмента нет.
const PISTON_LANG: Record<string, string> = {
  py: "python", js: "javascript", ts: "typescript", go: "go", rs: "rust", java: "java", kt: "kotlin",
  cs: "csharp", c: "c", cpp: "c++", cc: "c++", rb: "ruby", php: "php", swift: "swift", hs: "haskell",
  lua: "lua", dart: "dart", scala: "scala", zig: "zig", sh: "bash", pl: "perl", r: "rscript",
  ex: "elixir", exs: "elixir", erl: "erlang", clj: "clojure", nim: "nim", jl: "julia", ml: "ocaml",
  pas: "pascal", f90: "fortran", cr: "crystal", d: "d", groovy: "groovy", sql: "sqlite3",
};

/** Подсказки команд по файлам в корне проекта. */
function suggestions(files: FileNode[]): string[] {
  const names = new Set(files.map(f => f.name));
  const out: string[] = [];
  if (names.has("requirements.txt")) out.push("pip install -r requirements.txt");
  if (names.has("pyproject.toml")) out.push("pip install -e .");
  if (names.has("requirements.txt") || names.has("pyproject.toml") || names.has("tests")) out.push("python -m pytest -q");
  if (names.has("package.json")) out.push("npm install", "npm test", "npm run build");
  if (names.has("go.mod")) out.push("go test ./...");
  if (names.has("Cargo.toml")) out.push("cargo test");
  if (names.has("Makefile")) out.push("make");
  if (!out.length) out.push("ls -la");
  return out;
}

export function ProjectTerminal({ projectId, request }: { projectId: string; request: RunRequest | null }) {
  const status = useQuery({ queryKey: ["sandbox-status"], queryFn: () => api.get<SandboxStatus>("/sandbox/status"), staleTime: 30_000 });
  const root = useQuery({
    queryKey: ["project-files", projectId, "."],
    queryFn: () => api.get<FileNode[]>(`/projects/${projectId}/files?path=.`),
  });
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [timeout, setTimeoutValue] = useState(120);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [past, setPast] = useState<string[]>([]);
  const cursor = useRef(-1);
  const abort = useRef<AbortController | null>(null);
  const handled = useRef(0);
  const list = useRef<HTMLDivElement>(null);
  const box = status.data?.sandbox;
  const tools = box?.tools || {};
  const runtimes = status.data?.piston.runtimes || [];

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => { list.current?.scrollTo({ top: list.current.scrollHeight }); }, [entries.length]);

  function update(id: string, patch: Partial<Entry> | ((entry: Entry) => Partial<Entry>)) {
    setEntries(old => old.map(e => (e.id === id ? { ...e, ...(typeof patch === "function" ? patch(e) : patch) } : e)));
  }

  async function run(command: string) {
    const text = command.trim();
    if (!text || running) return;
    setError(null);
    setInput("");
    setPast(old => [text, ...old.filter(c => c !== text)].slice(0, 50));
    cursor.current = -1;
    const id = `t${Date.now()}`;
    setEntries(old => [...old.slice(-30), { id, name: "run_command", path: "", command: text, status: "running", output: "", started: Date.now() }]);
    const controller = new AbortController();
    abort.current = controller;
    setRunning(true);
    try {
      await runInProject(projectId, { command: text, timeout }, event => {
        if (event.type === "output") update(id, e => ({ output: ((e.output || "") + event.data).slice(-200_000) }));
        else if (event.type === "info") update(id, { note: event.data });
        else if (event.type === "error") update(id, { status: "error", error: event.data });
        else if (event.type === "exit") update(id, { status: "done", exit_code: event.code, duration_s: event.duration, timed_out: event.timed_out });
      }, controller.signal);
      update(id, e => (e.status === "running" ? { status: "error", error: "Соединение прервалось" } : {}));
    } catch (e) {
      const stopped = controller.signal.aborted;
      update(id, { status: "error", error: stopped ? "Остановлено вами" : e instanceof Error ? e.message : "Не удалось выполнить команду" });
    } finally {
      abort.current = null;
      setRunning(false);
    }
  }

  async function runWithPiston(path: string, language: string) {
    const started = Date.now();
    const id = `t${started}`;
    setEntries(old => [...old.slice(-30), { id, name: "run_code", path, command: `${language}: ${path}`, status: "running", output: "", started }]);
    setRunning(true);
    try {
      const result = await api.post<RunCodeResult>(`/projects/${projectId}/run-code`, { language, paths: [path] });
      update(id, { status: "done", exit_code: result.exit_code ?? null, output: result.shown || result.output || "", duration_s: (Date.now() - started) / 1000 });
    } catch (e) {
      update(id, { status: "error", error: e instanceof ApiError ? e.message : "Не удалось запустить" });
    } finally {
      setRunning(false);
    }
  }

  function runFile(path: string) {
    const ext = path.split(".").pop()?.toLowerCase() || "";
    const local = SANDBOX_RUN[ext];
    if (local && box?.available && (!local.tool || tools[local.tool])) return run(local.command(path));
    const language = PISTON_LANG[ext];
    if (language && runtimes.some(r => r.language === language || r.aliases?.includes(language))) return runWithPiston(path, language);
    setError(`Нечем запустить .${ext}: ${box?.available ? "в песочнице нет этого языка" : "песочница недоступна"}${language ? `, а в Piston не установлен ${language}` : ""}. Языки добавляются в Настройки → Песочница.`);
  }

  useEffect(() => {
    if (!request || request.id === handled.current || status.isPending) return;
    handled.current = request.id;
    runFile(request.path);
    // runFile читает актуальные status/tools — запуск по новой заявке, не по каждому рендеру.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, status.isPending]);

  async function reset() {
    if (!(await confirmAction("Сбросить окружение проекта в песочнице? Установленные зависимости (.venv, node_modules) и сборки удалятся; файлы проекта не затрагиваются."))) return;
    setError(null);
    try {
      await api.del(`/projects/${projectId}/sandbox`);
      setEntries(old => [...old, { id: `r${Date.now()}`, name: "run_command", path: "", command: "окружение сброшено", status: "done", exit_code: 0, output: "Зависимости поставятся заново при следующей команде.", started: Date.now() }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сбросить окружение");
    }
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") { e.preventDefault(); run(input); }
    else if (e.key === "ArrowUp" && past.length) {
      e.preventDefault();
      cursor.current = Math.min(cursor.current + 1, past.length - 1);
      setInput(past[cursor.current]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      cursor.current = Math.max(cursor.current - 1, -1);
      setInput(cursor.current >= 0 ? past[cursor.current] : "");
    }
  }

  const unavailable = status.isSuccess && !box?.available;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-700 px-3 py-2 text-[11px] text-neutral-400">
        <SquareTerminal className="h-4 w-4 text-accent-300" />
        <span className="font-medium text-neutral-200">Песочница проекта</span>
        {status.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : box?.available ? <>
          <span className="min-w-0 truncate">{Object.entries(tools).filter(([name]) => !["pip", "npm", "make", "git", "cargo", "mvn"].includes(name)).map(([name, version]) => `${name} ${version.replace(/^[^\d]*/, "").split(" ")[0]}`).join(" · ")}</span>
          <span className="flex items-center gap-1" title="Интернет из песочницы"><Globe className="h-3 w-3" />{box.network === "proxy" ? "реестры пакетов" : "без сети"}</span>
        </> : <span className="text-amber-300">недоступна</span>}
        <span className="flex-1" />
        <select aria-label="Предел времени команды" value={timeout} onChange={e => setTimeoutValue(Number(e.target.value))} className="rounded-md bg-ink-900 px-2 py-1">
          {TIMEOUTS.map(t => <option key={t} value={t}>до {t >= 60 ? `${t / 60} мин` : `${t} с`}</option>)}
        </select>
        <button onClick={reset} disabled={running || !box?.available} title="Удалить зависимости и сборки проекта в песочнице" className="icon-button !h-7 !w-7"><RotateCcw className="h-3.5 w-3.5" /></button>
        <button onClick={() => setEntries([])} disabled={running || !entries.length} title="Очистить вывод" className="icon-button !h-7 !w-7"><Trash2 className="h-3.5 w-3.5" /></button>
      </div>
      <div ref={list} className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {unavailable && <div className="rounded-xl border border-amber-400/30 bg-amber-500/5 p-4 text-xs leading-relaxed text-amber-100">
          Песочница не запущена. Она поднимается вместе с Layla (сервис <code>sandbox</code> в docker compose) — обновите установку по инструкции и перезапустите.
        </div>}
        {!entries.length && !unavailable && <div className="px-2 py-6 text-center text-xs leading-relaxed text-neutral-500">
          Команды выполняются в копии проекта: можно ставить зависимости, запускать тесты и сборку. Изменения, которые делают команды, в проект не сохраняются.
        </div>}
        {entries.map(entry => <div key={entry.id} className={`overflow-hidden rounded-lg border text-xs ${entry.status === "error" || (entry.status === "done" && entry.exit_code !== 0) ? "border-red-500/30" : "border-ink-700"}`}>
          <div className="flex items-center gap-2 px-3 py-2">
            <code className="min-w-0 flex-1 break-all font-mono text-neutral-200">{entry.name === "run_command" ? "$ " : ""}{entry.command}</code>
            {entry.duration_s !== undefined && entry.status !== "running" && entry.duration_s > 0 && <span className="text-neutral-500">{seconds(entry.duration_s)}</span>}
            <RunBadge tool={entry} />
          </div>
          {entry.status === "running" && entry.note && !entry.output && <p className="border-t border-ink-700 px-3 py-2 text-neutral-500">{entry.note}</p>}
          {(entry.output || entry.status !== "running") && <div className="border-t border-ink-700"><OutputPane text={entry.output || ""} live={entry.status === "running"} className="max-h-[28rem]" /></div>}
          {entry.error && <p className="border-t border-red-500/20 px-3 py-2 text-red-300">{entry.error}</p>}
        </div>)}
      </div>
      {error && <p role="alert" className="mx-3 mb-2 text-xs text-red-300">{error}</p>}
      <div className="border-t border-ink-700 p-3">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {suggestions(root.data || []).map(cmd => <button key={cmd} onClick={() => setInput(cmd)} disabled={running} className="rounded-md border border-ink-700 px-2 py-1 font-mono text-[11px] text-neutral-400 hover:border-accent-500/50 hover:text-neutral-200">{cmd}</button>)}
        </div>
        <div className="flex items-center gap-2 rounded-xl bg-ink-900 px-3 py-2 font-mono text-xs">
          <span className="text-accent-300">$</span>
          <input aria-label="Команда" value={input} onChange={e => setInput(e.target.value)} onKeyDown={onKey} disabled={unavailable} placeholder={running ? "Команда выполняется…" : "pytest -q"} className="min-w-0 flex-1 bg-transparent outline-none" autoComplete="off" spellCheck={false} />
          {running && abort.current
            ? <button onClick={() => abort.current?.abort()} aria-label="Остановить команду" className="icon-button !h-7 !w-7 text-red-300"><Square className="h-3 w-3" /></button>
            : <button onClick={() => run(input)} disabled={!input.trim() || running || unavailable} aria-label="Выполнить" className="primary-button !h-7 !w-7 !p-0"><Play className="h-3.5 w-3.5" /></button>}
        </div>
      </div>
    </div>
  );
}
