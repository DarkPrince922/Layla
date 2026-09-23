"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, Download, Globe, Loader2, RefreshCw, Search, SquareTerminal, Trash2 } from "lucide-react";
import { api, type Job, type PistonPackage, type SandboxStatus } from "@/lib/api";
import { useAuth } from "@/store/auth";
import { confirmAction } from "@/components/ConfirmDialog";

const chip = "rounded-lg border border-ink-700 bg-ink-900/60 px-2.5 py-1 font-mono text-[11px] text-neutral-300";
const card = "rounded-2xl border border-ink-700/70 bg-ink-800/30 p-5";

function Dot({ ok }: { ok: boolean }) {
  return <span className={`inline-block h-2 w-2 rounded-full ${ok ? "bg-emerald-400" : "bg-amber-400"}`} />;
}

export function SandboxSettings() {
  const qc = useQueryClient();
  const admin = !!useAuth(s => s.user?.is_admin);
  const status = useQuery({ queryKey: ["sandbox-status"], queryFn: () => api.get<SandboxStatus>("/sandbox/status") });
  const piston = status.data?.piston;
  const packages = useQuery({
    queryKey: ["piston-packages"],
    queryFn: () => api.get<PistonPackage[]>("/sandbox/languages"),
    enabled: !!piston?.available,
  });
  const [query, setQuery] = useState("");
  const [installing, setInstalling] = useState<Record<string, string>>({}); // пакет -> id задачи
  const [error, setError] = useState<string | null>(null);

  // Установка идёт в фоне: следим за задачами, пока они не закончатся.
  const jobIds = Object.values(installing);
  useQuery({
    queryKey: ["sandbox-install-jobs", jobIds],
    enabled: jobIds.length > 0,
    refetchInterval: 1500,
    queryFn: async () => {
      const jobs = await Promise.all(jobIds.map(id => api.get<Job>(`/jobs/${id}`)));
      const finished = jobs.filter(j => !["queued", "running"].includes(j.status));
      if (finished.length) {
        const failed = finished.find(j => j.status === "error");
        if (failed) setError(failed.error || "Не удалось установить язык");
        setInstalling(old => Object.fromEntries(Object.entries(old).filter(([, id]) => !finished.some(j => j.id === id))));
        await Promise.all([qc.invalidateQueries({ queryKey: ["sandbox-status"] }), qc.invalidateQueries({ queryKey: ["piston-packages"] })]);
      }
      return jobs;
    },
  });

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (packages.data || [])
      .filter(p => !q || p.language.toLowerCase().includes(q))
      .sort((a, b) => Number(b.installed) - Number(a.installed) || a.language.localeCompare(b.language)
        || b.language_version.localeCompare(a.language_version, undefined, { numeric: true }));
  }, [packages.data, query]);

  async function install(p: PistonPackage) {
    setError(null);
    try {
      const job = await api.post<Job>("/sandbox/languages", { language: p.language, version: p.language_version });
      setInstalling(old => ({ ...old, [`${p.language}=${p.language_version}`]: job.id }));
      await qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось начать установку");
    }
  }

  async function remove(p: PistonPackage) {
    if (!(await confirmAction(`Удалить ${p.language} ${p.language_version} из Piston?`, "Удалить"))) return;
    setError(null);
    try {
      await api.del("/sandbox/languages", { language: p.language, version: p.language_version });
      await Promise.all([qc.invalidateQueries({ queryKey: ["sandbox-status"] }), qc.invalidateQueries({ queryKey: ["piston-packages"] })]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить язык");
    }
  }

  const box = status.data?.sandbox;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold">Песочница</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Где агент и вы запускаете код: команды с зависимостями — в песочнице проекта, программы на других языках — в Piston.
          </p>
        </div>
        <button onClick={() => { status.refetch(); packages.refetch(); }} className="secondary-button text-xs"><RefreshCw className={`h-4 w-4 ${status.isFetching ? "animate-spin" : ""}`} />Проверить</button>
      </div>
      {error && <p role="alert" className="text-sm text-red-300">{error}</p>}

      <section className={card}>
        <div className="flex items-center gap-2">
          <SquareTerminal className="h-5 w-5 text-accent-300" />
          <h2 className="flex-1 font-semibold">Песочница проекта</h2>
          {status.isPending ? <Loader2 className="h-4 w-4 animate-spin text-neutral-500" /> : <span className="flex items-center gap-2 text-xs text-neutral-400"><Dot ok={!!box?.available} />{box?.available ? "Работает" : "Недоступна"}</span>}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-neutral-400">
          Команды выполняются в копии файлов проекта: <code>pip install</code>, <code>npm install</code>, тесты, сборка. Зависимости остаются между запусками,
          а в сам проект изменения из песочницы не попадают. У каждого пользователя своя изолированная учётная запись внутри песочницы.
        </p>
        {box?.available ? <>
          <div className="mt-4 flex flex-wrap gap-2">{Object.entries(box.tools || {}).map(([name, version]) => <span key={name} className={chip} title={version}>{name} · {version.replace(/^[^\d]*/, "").split(" ")[0] || version}</span>)}</div>
          <div className="mt-4 grid gap-3 text-xs sm:grid-cols-2">
            <div className="rounded-xl bg-ink-900/50 p-3">
              <p className="flex items-center gap-1.5 font-medium text-neutral-200"><Globe className="h-3.5 w-3.5" />Интернет</p>
              <p className="mt-1 leading-relaxed text-neutral-400">{box.network === "proxy"
                ? "Только реестры пакетов (PyPI, npm, Go, crates.io, Maven) через прокси. Другие адреса и локальная сеть закрыты. Свои хосты — SANDBOX_ALLOW_HOSTS в .env."
                : "Выключен: зависимости не поставить."}</p>
            </div>
            <div className="rounded-xl bg-ink-900/50 p-3">
              <p className="font-medium text-neutral-200">Пределы</p>
              <p className="mt-1 leading-relaxed text-neutral-400">Команда — до {Math.round((box.limits?.max_timeout || 900) / 60)} мин, вывод — до {Math.round((box.limits?.max_output || 0) / 1000)} КБ, одновременно — {box.limits?.max_parallel || 4}. Память и CPU — SANDBOX_MEMORY и SANDBOX_CPUS в .env.</p>
            </div>
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-neutral-500">Go, Rust и Java с зависимостями: SANDBOX_WITH_GO / SANDBOX_WITH_RUST / SANDBOX_WITH_JAVA=true в .env, затем пересборка.</p>
        </> : !status.isPending && <p className="mt-3 rounded-xl border border-amber-400/30 bg-amber-500/5 p-3 text-xs leading-relaxed text-amber-100">
          Сервис <code>sandbox</code> не отвечает. Он входит в docker compose Layla: обновите установку и пересоберите контейнеры. Логи: <code>docker compose logs sandbox</code>.
        </p>}
      </section>

      <section className={card}>
        <div className="flex items-center gap-2">
          <Cpu className="h-5 w-5 text-accent-300" />
          <h2 className="flex-1 font-semibold">Piston — другие языки</h2>
          {status.isPending ? <Loader2 className="h-4 w-4 animate-spin text-neutral-500" /> : <span className="flex items-center gap-2 text-xs text-neutral-400"><Dot ok={!!piston?.available} />{piston?.available ? `Языков: ${piston.runtimes.length}` : "Недоступен"}</span>}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-neutral-400">
          Компиляция и запуск программ на десятках языков — Go, Rust, Java, C#, Kotlin, Haskell и других. Каждая программа идёт в чистой изолированной
          среде без сети и без сторонних библиотек. Агент видит только установленные языки.
        </p>
        {!piston?.available && !status.isPending && <p className="mt-3 rounded-xl border border-amber-400/30 bg-amber-500/5 p-3 text-xs leading-relaxed text-amber-100">
          Сервис <code>piston</code> не отвечает. Ему нужен режим privileged и cgroup v2 на хосте. Логи: <code>docker compose logs piston</code>.
        </p>}
        {piston?.available && <>
          {!admin && <p className="mt-3 text-xs text-neutral-500">Устанавливать и удалять языки может администратор.</p>}
          <label className="mt-4 flex items-center gap-2 rounded-xl bg-ink-900 px-3 py-2 text-xs">
            <Search className="h-3.5 w-3.5 text-neutral-500" />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Найти язык: rust, go, java…" aria-label="Поиск языка" className="min-w-0 flex-1 bg-transparent outline-none" />
          </label>
          {packages.isPending && <p className="mt-3 text-xs text-neutral-500">Загрузка списка языков…</p>}
          {packages.isError && <p className="mt-3 text-xs text-red-300">Не удалось получить список языков из Piston.</p>}
          <div className="mt-3 max-h-[28rem] divide-y divide-ink-700/60 overflow-y-auto rounded-xl border border-ink-700/70">
            {shown.map(p => {
              const key = `${p.language}=${p.language_version}`;
              const busy = !!installing[key];
              return <div key={key} className="flex items-center gap-3 px-3 py-2 text-xs">
                <span className="min-w-0 flex-1 truncate font-mono text-neutral-200">{p.language}</span>
                <span className="font-mono text-neutral-500">{p.language_version}</span>
                {p.installed ? <>
                  <span className="text-emerald-300">установлен</span>
                  {admin && <button onClick={() => remove(p)} aria-label={`Удалить ${p.language} ${p.language_version}`} className="icon-button !h-7 !w-7 hover:text-red-300"><Trash2 className="h-3.5 w-3.5" /></button>}
                </> : admin && <button onClick={() => install(p)} disabled={busy} className="secondary-button !px-2.5 !py-1 text-[11px]">
                  {busy ? <><Loader2 className="h-3 w-3 animate-spin" />Ставлю…</> : <><Download className="h-3 w-3" />Установить</>}
                </button>}
              </div>;
            })}
            {packages.isSuccess && !shown.length && <p className="p-4 text-center text-xs text-neutral-500">Ничего не нашлось.</p>}
          </div>
        </>}
      </section>
    </div>
  );
}
