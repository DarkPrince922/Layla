"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownToLine, ArrowUpFromLine, ChevronDown, GitBranch, GitCommitHorizontal, History, KeyRound, Link2,
  Loader2, RefreshCw, Trash2,
} from "lucide-react";
import { api, splitGitDiff, type GitCommit, type GitCredential, type GitStatus } from "@/lib/api";
import { FileDiff } from "@/components/FileDiff";
import { confirmAction } from "@/components/ConfirmDialog";

const STATUS: Record<string, { label: string; tone: string }> = {
  new: { label: "новый", tone: "text-emerald-300" },
  added: { label: "добавлен", tone: "text-emerald-300" },
  modified: { label: "изменён", tone: "text-amber-200" },
  deleted: { label: "удалён", tone: "text-red-300" },
  renamed: { label: "переименован", tone: "text-sky-300" },
};

function when(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

const card = "rounded-2xl border border-ink-700/70 bg-ink-800/25 p-4";

export function GitPanel({ projectId, onOpenFile }: { projectId: string; onOpenFile?: (path: string) => void }) {
  const qc = useQueryClient();
  const key = ["git", projectId];
  const status = useQuery({ queryKey: key, queryFn: () => api.get<GitStatus>(`/projects/${projectId}/git`) });
  const history = useQuery({
    queryKey: ["git-log", projectId],
    enabled: !!status.data?.initialized,
    queryFn: () => api.get<GitCommit[]>(`/projects/${projectId}/git/log?limit=50`),
  });
  const credentials = useQuery({ queryKey: ["git-credentials"], queryFn: () => api.get<GitCredential[]>("/git/credentials") });
  const [message, setMessage] = useState("");
  const [remote, setRemote] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [opened, setOpened] = useState<{ sha: string; diff: string; message: string } | null>(null);

  const state = status.data;
  const changes = state?.changes || [];
  const host = state?.remote_host || null;
  const credential = credentials.data?.find(c => c.host === host);

  async function act(name: string, action: () => Promise<unknown>, done?: string) {
    setBusy(name); setError(null); setNotice(null);
    try {
      await action();
      if (done) setNotice(done);
      setDiff(null);
      await Promise.all([qc.invalidateQueries({ queryKey: key }), qc.invalidateQueries({ queryKey: ["git-log", projectId] }),
        qc.invalidateQueries({ queryKey: ["git-credentials"] }), qc.invalidateQueries({ queryKey: ["project-files", projectId] })]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не получилось");
    } finally { setBusy(null); }
  }

  async function showDiff() {
    if (diff !== null) { setDiff(null); return; }
    setBusy("diff"); setError(null);
    try { setDiff((await api.get<{ diff: string }>(`/projects/${projectId}/git/diff`)).diff); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось получить изменения"); }
    finally { setBusy(null); }
  }

  async function openCommit(sha: string) {
    if (opened?.sha === sha) { setOpened(null); return; }
    setBusy(sha); setError(null);
    try {
      const data = await api.get<{ diff: string; message: string }>(`/projects/${projectId}/git/commits/${sha}`);
      setOpened({ sha, diff: data.diff, message: data.message });
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось открыть коммит"); }
    finally { setBusy(null); }
  }

  if (status.isPending) return <p className="p-6 text-sm text-neutral-500">Загрузка…</p>;
  if (status.isError) return <p role="alert" className="p-6 text-sm text-red-300">Не удалось получить состояние Git: {status.error.message}</p>;

  if (!state?.initialized) return (
    <div className="grid flex-1 place-items-center p-8 text-center">
      <div className="max-w-sm">
        <span className="empty-orb"><GitBranch className="h-6 w-6" /></span>
        <h2 className="text-lg font-semibold">Проект пока не под Git</h2>
        <p className="mt-3 text-sm leading-relaxed text-neutral-400">Включите Git, чтобы сохранять версии проекта коммитами, смотреть историю изменений и отправлять код в GitHub.</p>
        <button className="primary-button mt-5" disabled={!!busy} onClick={() => act("init", () => api.post(`/projects/${projectId}/git/init`), "Git включён")}>
          {busy === "init" ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}Включить Git
        </button>
        {error && <p role="alert" className="mt-3 text-xs text-red-300">{error}</p>}
      </div>
    </div>
  );

  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="flex items-center gap-1.5 rounded-lg bg-ink-900 px-2.5 py-1.5 font-mono text-neutral-200"><GitBranch className="h-3.5 w-3.5 text-accent-300" />{state.branch || "main"}</span>
        {!!state.ahead && <span className="rounded-lg bg-accent-500/15 px-2 py-1 text-accent-100">↑ {state.ahead} не отправлено</span>}
        {!!state.behind && <span className="rounded-lg bg-amber-500/15 px-2 py-1 text-amber-100">↓ {state.behind} на сервере</span>}
        <span className="min-w-0 flex-1 truncate text-neutral-500" title={state.remote || ""}>{state.remote ? state.remote.replace(/^https:\/\//, "") : "без удалённого репозитория"}</span>
        <button onClick={() => { status.refetch(); history.refetch(); }} className="icon-button !h-8 !w-8" aria-label="Обновить"><RefreshCw className={`h-3.5 w-3.5 ${status.isFetching ? "animate-spin" : ""}`} /></button>
        {state.remote && <>
          <button className="secondary-button !px-3 !py-1.5 text-xs" disabled={!!busy} onClick={() => act("pull", () => api.post(`/projects/${projectId}/git/pull`), "Изменения с сервера получены")}>
            {busy === "pull" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowDownToLine className="h-3.5 w-3.5" />}Pull
          </button>
          <button className="primary-button !px-3 !py-1.5 text-xs" disabled={!!busy || !state.last_commit} onClick={async () => {
            if (await confirmAction(`Отправить коммиты ветки ${state.branch || "main"} в ${state.remote}?`, "Отправить")) act("push", () => api.post(`/projects/${projectId}/git/push`, {}), "Коммиты отправлены");
          }}>
            {busy === "push" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUpFromLine className="h-3.5 w-3.5" />}Push
          </button>
        </>}
      </div>
      {error && <p role="alert" className="rounded-xl border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-300">{error}</p>}
      {notice && <p role="status" className="text-xs text-emerald-300">{notice}</p>}

      <section className={card}>
        <div className="flex items-center gap-2">
          <h3 className="flex-1 text-sm font-semibold">Изменения {changes.length ? `· ${changes.length}` : ""}</h3>
          {!!changes.length && <button onClick={showDiff} className="flex items-center gap-1 text-xs text-accent-200 hover:text-white">{busy === "diff" ? <Loader2 className="h-3 w-3 animate-spin" /> : <ChevronDown className={`h-3 w-3 transition-transform ${diff !== null ? "rotate-180" : ""}`} />}{diff !== null ? "Скрыть дифф" : "Показать дифф"}</button>}
        </div>
        {!changes.length ? <p className="mt-2 text-xs text-neutral-500">Всё закоммичено.</p> : diff === null ? (
          <ul className="mt-3 max-h-48 space-y-1 overflow-y-auto text-xs">
            {changes.map(c => <li key={c.path} className="flex items-center gap-2">
              <span className={`w-24 shrink-0 ${STATUS[c.status]?.tone || ""}`}>{STATUS[c.status]?.label || c.code}</span>
              <button onClick={() => c.status !== "deleted" && onOpenFile?.(c.path)} className="min-w-0 truncate text-left font-mono text-neutral-300 hover:text-white">{c.path}</button>
            </li>)}
          </ul>
        ) : <div className="mt-2">{splitGitDiff(diff).map(change => <FileDiff key={change.path} change={change} onOpen={onOpenFile} />)}</div>}
        {!!changes.length && <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input value={message} onChange={e => setMessage(e.target.value)} placeholder="Что изменилось — сообщение коммита" aria-label="Сообщение коммита"
            onKeyDown={e => { if (e.key === "Enter" && message.trim()) act("commit", () => api.post(`/projects/${projectId}/git/commit`, { message }).then(() => setMessage("")), "Коммит создан"); }}
            className="min-w-0 flex-1 rounded-xl bg-ink-900 px-3 py-2 text-xs outline-none" />
          <button className="primary-button !px-3 !py-2 text-xs" disabled={!message.trim() || !!busy}
            onClick={() => act("commit", () => api.post(`/projects/${projectId}/git/commit`, { message }).then(() => setMessage("")), "Коммит создан")}>
            {busy === "commit" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitCommitHorizontal className="h-3.5 w-3.5" />}Закоммитить всё
          </button>
        </div>}
      </section>

      <section className={card}>
        <h3 className="flex items-center gap-2 text-sm font-semibold"><History className="h-4 w-4 text-accent-300" />История</h3>
        {!history.data?.length ? <p className="mt-2 text-xs text-neutral-500">Коммитов пока нет.</p> : (
          <ul className="mt-2 divide-y divide-ink-700/60">
            {history.data.map(commit => <li key={commit.sha} className="py-2">
              <button onClick={() => openCommit(commit.sha)} className="flex w-full items-center gap-3 text-left text-xs">
                <code className="shrink-0 text-accent-200">{commit.short}</code>
                <span className="min-w-0 flex-1 truncate text-neutral-200">{commit.message}</span>
                <span className="hidden shrink-0 text-neutral-500 sm:inline">{commit.author}</span>
                <span className="shrink-0 text-neutral-500">{when(commit.date)}</span>
                {busy === commit.sha ? <Loader2 className="h-3 w-3 animate-spin" /> : <ChevronDown className={`h-3 w-3 shrink-0 text-neutral-500 transition-transform ${opened?.sha === commit.sha ? "rotate-180" : ""}`} />}
              </button>
              {opened?.sha === commit.sha && <div className="mt-2">
                {opened.message.includes("\n") && <p className="mb-2 whitespace-pre-wrap text-xs text-neutral-400">{opened.message}</p>}
                {splitGitDiff(opened.diff).map(change => <FileDiff key={change.path} change={change} />)}
              </div>}
            </li>)}
          </ul>
        )}
      </section>

      <section className={card}>
        <h3 className="flex items-center gap-2 text-sm font-semibold"><Link2 className="h-4 w-4 text-accent-300" />Удалённый репозиторий</h3>
        <p className="mt-1 text-xs text-neutral-500">Адрес https, например https://github.com/user/repo.git. Репозиторий на GitHub создаётся заранее (можно пустой).</p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input value={remote} onChange={e => setRemote(e.target.value)} placeholder={state.remote || "https://github.com/user/repo.git"} aria-label="Адрес удалённого репозитория"
            className="min-w-0 flex-1 rounded-xl bg-ink-900 px-3 py-2 font-mono text-xs outline-none" />
          <button className="secondary-button !px-3 !py-2 text-xs" disabled={!remote.trim() || !!busy}
            onClick={() => act("remote", () => api.put(`/projects/${projectId}/git/remote`, { url: remote.trim() }).then(() => setRemote("")), "Адрес сохранён")}>
            {state.remote ? "Изменить" : "Подключить"}
          </button>
        </div>
        {host && <div className="mt-4 rounded-xl bg-ink-900/60 p-3 text-xs">
          <p className="flex items-center gap-2 font-medium text-neutral-200"><KeyRound className="h-3.5 w-3.5 text-accent-300" />Токен доступа для {host}</p>
          {credential ? <div className="mt-2 flex items-center gap-2 text-neutral-400">
            <span className="font-mono">{credential.masked}</span><span className="flex-1">— задан, хранится зашифрованным</span>
            <button onClick={async () => { if (await confirmAction(`Удалить токен для ${host}?`, "Удалить")) act("token", () => api.del(`/git/credentials/${host}`), "Токен удалён"); }} className="icon-button !h-7 !w-7 hover:text-red-300" aria-label="Удалить токен"><Trash2 className="h-3.5 w-3.5" /></button>
          </div> : <>
            <p className="mt-1 leading-relaxed text-neutral-500">
              Нужен для push и для pull из приватного репозитория. На GitHub: Settings → Developer settings → Fine-grained tokens,
              доступ к нужному репозиторию, права Contents — Read and write. Один токен работает для всех проектов на этом хостинге.
            </p>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <input type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="github_pat_…" aria-label="Токен доступа" autoComplete="off"
                className="min-w-0 flex-1 rounded-xl bg-ink-950 px-3 py-2 font-mono text-xs outline-none" />
              <button className="secondary-button !px-3 !py-2 text-xs" disabled={token.trim().length < 4 || !!busy}
                onClick={() => act("token", () => api.put("/git/credentials", { host, token: token.trim() }).then(() => setToken("")), "Токен сохранён")}>
                Сохранить токен
              </button>
            </div>
          </>}
        </div>}
      </section>
    </div>
  );
}
