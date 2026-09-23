"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  ArrowLeft, ArrowRight, ChevronDown, Code2, Columns2, Eye, History, Loader2, Monitor, Palette, Smartphone,
  Sparkles, Tablet, Trash2,
} from "lucide-react";
import { ChatPanel } from "@/components/ChatPanel";
import { DesignBriefForm } from "@/components/DesignBriefForm";
import { CodeStream, DesignLive, isActive } from "@/components/DesignLive";
import { api, type Chat, type Design, type FileContent, type FileNode, type Job, type ModelInfo, type Project } from "@/lib/api";
import { DEFAULT_BRIEF, briefPayload, loadBrief, saveBrief, type Brief } from "@/lib/design-brief";
import { useAuth } from "@/store/auth";
import { confirmAction } from "@/components/ConfirmDialog";

const BREAKPOINTS = { desktop: "100%", tablet: "768px", mobile: "390px" } as const;

/** Что показываем в превью: файлы открытого чата или сохранённую версию брифа. */
type Source = { kind: "chat" } | { kind: "design"; id: string };

export function DesignDomain() {
  const qc = useQueryClient();
  const router = useRouter();
  const [brief, setBrief] = useState<Brief>(DEFAULT_BRIEF);
  const briefLoaded = useRef(false);
  const [stack, setStack] = useState("html");
  const [model, setModel] = useState("");
  const [briefOpen, setBriefOpen] = useState(false);
  const [source, setSource] = useState<Source>({ kind: "chat" });
  const [chatProject, setChatProject] = useState<string | null>(null);
  const [view, setView] = useState<"preview" | "code" | "split">("preview");
  const [bp, setBp] = useState<keyof typeof BREAKPOINTS>("desktop");
  const [pane, setPane] = useState<"chat" | "result">("chat");
  const [busy, setBusy] = useState(false);
  const versionsMenu = useRef<HTMLDetailsElement>(null);
  const [error, setError] = useState<string | null>(null);
  // Генерация по брифу, которую показываем вживую; watching=false — пользователь смотрит другое.
  const [liveJobId, setLiveJobId] = useState<string | null>(null);
  const [watching, setWatching] = useState(false);

  const { data: models = [] } = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelInfo[]>("/models") });
  const { data: designs = [], isSuccess: designsLoaded } = useQuery({ queryKey: ["designs"], queryFn: () => api.get<Design[]>("/designs") });
  // Тот же ключ, что у истории в ChatPanel, — общий кеш, без лишнего запроса.
  const owner = useAuth(s => s.user?.id);
  const { data: chats = [], isSuccess: chatsLoaded } = useQuery({
    queryKey: ["chats", owner, "design", undefined],
    queryFn: () => api.get<Chat[]>("/chats?domain=design"),
  });

  // Файлы чата: их пишет агент, поэтому превью показывает актуальный результат.
  const tree = useQuery({
    queryKey: ["design-files", chatProject],
    enabled: !!chatProject,
    queryFn: () => api.get<FileNode[]>(`/projects/${chatProject}/files`),
  });
  const page = useMemo(() => {
    const list = (tree.data || []).filter(f => !f.is_dir);
    return list.find(f => f.name === "index.html") || list.find(f => f.name.endsWith(".html")) || null;
  }, [tree.data]);
  const live = useQuery({
    queryKey: ["design-file", chatProject, page?.path],
    enabled: !!chatProject && !!page,
    queryFn: () => api.get<FileContent>(`/projects/${chatProject}/file?path=${encodeURIComponent(page!.path)}`),
  });

  const liveJob = useQuery({
    queryKey: ["design-job", liveJobId],
    enabled: !!liveJobId,
    queryFn: () => api.get<Job>(`/jobs/${liveJobId}`),
    refetchInterval: q => (!q.state.data || isActive(q.state.data) ? 700 : false),
    refetchIntervalInBackground: true,
  });
  // Генерация, начатая раньше (до перезагрузки страницы или в другом разделе), — продолжаем показывать.
  const running = useQuery({
    queryKey: ["design-running-jobs"],
    queryFn: () => api.get<Job[]>("/jobs?active=true&limit=50"),
    // Только при открытии раздела и всегда свежий список: старый кеш вернул бы давно законченную задачу.
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });

  const generate = useMutation({
    mutationFn: () => api.post<Job>("/designs/generate", { stack, brief: briefPayload(brief), model: model || undefined }),
    onSuccess: job => {
      setLiveJobId(job.id);
      setWatching(true);
      setView("split");
      setPane("result");
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setBriefOpen(false);
      setError(null);
    },
    onError: e => setError(e instanceof Error ? e.message : "Ошибка генерации"),
  });
  const stop = useMutation({
    mutationFn: (id: string) => api.post<Job>(`/jobs/${id}/cancel`),
    onSettled: () => liveJob.refetch(),
    onError: e => setError(e instanceof Error ? e.message : "Не удалось остановить"),
  });

  useEffect(() => {
    if (briefLoaded.current) { saveBrief(brief); return; }
    briefLoaded.current = true;
    setBrief(loadBrief());
  }, [brief]);
  useEffect(() => {
    const job = running.data?.find(j => j.kind === "design.generate");
    if (job) { setLiveJobId(id => id ?? job.id); setWatching(true); }
  }, [running.data]);
  // Генерация закончилась: готовую версию сразу показываем в превью.
  const finished = liveJob.data?.status === "done" ? liveJob.data.result.design_id : undefined;
  useEffect(() => {
    if (typeof finished !== "string") return;
    let cancelled = false;
    qc.invalidateQueries({ queryKey: ["designs"] }).then(() => {
      if (cancelled) return;
      setSource({ kind: "design", id: finished });
      setLiveJobId(null);
      setWatching(false);
    });
    qc.invalidateQueries({ queryKey: ["providers"] });  // модель могла подстроить свои настройки
    return () => { cancelled = true; };
  }, [finished, qc]);

  // Бриф раскрывается сам только на совсем пустом разделе. Раньше он открывался,
  // когда не было версий, и прятал чат — вместе с кнопками удаления и очистки истории.
  useEffect(() => {
    if (designsLoaded && chatsLoaded && !designs.length && !chats.length) setBriefOpen(true);
  }, [designsLoaded, chatsLoaded, designs.length, chats.length]);
  useEffect(() => {
    const linked = new URLSearchParams(window.location.search).get("design");
    if (linked && designs.some(d => d.id === linked)) setSource({ kind: "design", id: linked });
  }, [designs]);
  useEffect(() => {
    const mine = (e: Event) => (e as CustomEvent<{ domain?: string }>).detail?.domain === "design";
    // Клик по готовой генерации в «В работе» открывает именно эту версию.
    const result = (e: Event) => {
      if (!mine(e)) return;
      const detail = (e as CustomEvent<{ design_id?: unknown; job_id?: unknown }>).detail;
      if (typeof detail.design_id === "string") {
        setSource({ kind: "design", id: detail.design_id });
        setWatching(false);
      } else if (typeof detail.job_id === "string") {
        // Идущая или сорвавшаяся генерация — показываем её ход и черновик.
        setLiveJobId(detail.job_id);
        setWatching(true);
      }
      setPane("result");
    };
    const chat = (e: Event) => { if (mine(e)) setPane("chat"); };
    window.addEventListener("layla:open-workspace", result);
    window.addEventListener("layla:open-chat", chat);
    return () => { window.removeEventListener("layla:open-workspace", result); window.removeEventListener("layla:open-chat", chat); };
  }, []);

  // Агент дописал файл — перечитываем превью, чтобы оно не отставало от чата.
  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["design-files", chatProject] });
    qc.invalidateQueries({ queryKey: ["design-file", chatProject] });
  }, [qc, chatProject]);
  const bindProject = useCallback((id: string | null) => setChatProject(id), []);

  const showLive = !!liveJobId && watching;
  const picked = source.kind === "design" ? designs.find(d => d.id === source.id) || null : null;
  // Пока чат не создал страницу, показывать нечего — тогда видна последняя версия брифа.
  const version = picked || (page ? null : designs[0] || null);
  const html = version ? version.files?.[0]?.content ?? "" : live.data?.content ?? "";

  const versionLabel = (d: Design) => {
    const i = designs.findIndex(x => x.id === d.id);
    return `Версия ${designs.length - i} · ${String((d.brief as Record<string, string>)?.artifact_type || "Design")} · ${d.stack}`;
  };
  function choose(next: Source) {
    setSource(next);
    setWatching(false);
    if (versionsMenu.current) versionsMenu.current.open = false;
  }
  async function clearVersions() {
    if (!designs.length || !(await confirmAction(`Удалить все версии (${designs.length})? Проекты, созданные из них в «Коде», останутся.`))) return;
    setBusy(true);
    try {
      await api.del("/designs");
      setSource({ kind: "chat" });
      await qc.invalidateQueries({ queryKey: ["designs"] });
      if (versionsMenu.current) versionsMenu.current.open = false;
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить версии");
    } finally { setBusy(false); }
  }

  async function removeVersion(id: string) {
    if (!(await confirmAction("Удалить эту версию макета? Проект, созданный из неё, останется."))) return;
    setBusy(true);
    try {
      await api.del(`/designs/${id}`);
      if (source.kind === "design" && source.id === id) setSource({ kind: "chat" });
      await qc.invalidateQueries({ queryKey: ["designs"] });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить версию");
    } finally { setBusy(false); }
  }

  /** Передать увиденное в домен «Код»: там файловое дерево, редактор и агент. */
  async function openInCode() {
    setBusy(true);
    try {
      let projectId = chatProject;
      if (version) {
        const project = await api.post<Project>(`/designs/${version.id}/project`);
        projectId = project.id;
        await Promise.all([
          qc.invalidateQueries({ queryKey: ["designs"] }),
          qc.invalidateQueries({ queryKey: ["projects"] }),
        ]);
      }
      if (!projectId) { setError("Сначала сгенерируйте макет или начните чат."); return; }
      // Папка чата становится проектом «Кода»: появляется в списке и не удаляется вместе с чатом.
      if (!version) await api.post<Project>(`/projects/${projectId}/promote`);
      // Сбрасываем кеш списка проектов: «Код» загрузит его заново уже с этим проектом.
      qc.removeQueries({ queryKey: ["projects"] });
      router.push(`/code?project=${projectId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось передать макет в Код");
    } finally { setBusy(false); }
  }

  return (
    <div className="domain-workspace flex h-full min-w-0 flex-col">
      <header className="workspace-toolbar">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-500/10 text-accent-300"><Palette className="h-5 w-5" /></span>
        <div className="workspace-title"><h1>Дизайн</h1><p>Бриф даёт первый макет, чат его дорабатывает, «Код» превращает в сайт</p></div>
        <button className="secondary-button ml-auto text-xs" onClick={openInCode} disabled={busy || (!version && !chatProject)}>
          <Code2 className="h-4 w-4" /><span>Открыть в Коде</span>
        </button>
      </header>

      <div className="domain-columns" data-detail={pane === "result"}>
        {/* Левая колонка: быстрый старт по брифу + чат-доработка */}
        <div className="domain-list is-chat">
          <button className="domain-back items-center gap-2 px-4 py-3 text-sm text-neutral-400" onClick={() => setPane("result")}>Превью<ArrowRight className="h-4 w-4" /></button>
          {/* Бриф и чат раскрываются по очереди: вдвоём в одной колонке они
              зажимают друг друга, и поле ввода чата обрезается. */}
          <div className={`flex flex-col border-b border-ink-700/50 ${briefOpen ? "min-h-0 flex-1" : "shrink-0"}`}>
            <button onClick={() => setBriefOpen(v => !v)} aria-expanded={briefOpen} className="flex w-full items-center gap-2 px-4 py-3 text-sm text-neutral-300">
              <Sparkles className="h-4 w-4 text-accent-300" />
              <span className="flex-1 text-left">Быстрый старт по брифу</span>
              <ChevronDown className={`h-4 w-4 transition-transform ${briefOpen ? "rotate-180" : ""}`} />
            </button>
            {briefOpen && (
              <DesignBriefForm brief={brief} onChange={setBrief} stack={stack} onStack={setStack}
                model={model} onModel={setModel} models={models} pending={generate.isPending}
                onGenerate={() => generate.mutate()} />
            )}
          </div>
          {error && <p role="alert" className="border-b border-ink-700/50 px-4 py-2 text-xs text-red-300">{error}</p>}
          <div className={briefOpen ? "hidden" : "min-h-0 flex-1"}>
            <ChatPanel domain="design" onProject={bindProject} onFileChange={refresh} />
          </div>
        </div>

        {/* Правая колонка: живое превью того, что сейчас выбрано */}
        <div className="domain-detail">
          <button className="domain-back items-center gap-2 px-4 py-3 text-sm text-neutral-400" onClick={() => setPane("chat")}><ArrowLeft className="h-4 w-4" />К чату</button>
          <div className="flex flex-wrap items-center gap-2 border-b border-ink-700/60 px-4 py-3">
            <div className="flex gap-1">
              <ToolbarBtn active={view === "preview"} onClick={() => setView("preview")}><Eye className="h-3.5 w-3.5" /> Превью</ToolbarBtn>
              <ToolbarBtn active={view === "code"} onClick={() => setView("code")}><Code2 className="h-3.5 w-3.5" /> Код</ToolbarBtn>
              <ToolbarBtn active={view === "split"} label="Код и превью рядом" onClick={() => setView("split")}><Columns2 className="h-3.5 w-3.5" /><span className="hidden sm:inline">Вместе</span></ToolbarBtn>
            </div>
            {liveJob.data && !showLive && (
              <button onClick={() => setWatching(true)} className="flex items-center gap-1.5 rounded-lg bg-accent-500/15 px-2.5 py-1.5 text-xs text-accent-100 hover:bg-accent-500/25">
                {isActive(liveJob.data) && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {isActive(liveJob.data) ? `Генерация · ${Math.round(liveJob.data.progress * 100)}%` : "Последняя генерация"}
              </button>
            )}
            {/* Версии: выбор для превью и удаление — у каждой своя корзина. */}
            <details ref={versionsMenu} className="relative min-w-0">
              <summary aria-label="Версии и источник превью" className="flex cursor-pointer list-none items-center gap-1.5 rounded-lg bg-ink-900 px-2.5 py-1.5 text-xs [&::-webkit-details-marker]:hidden">
                <History className="h-3.5 w-3.5 shrink-0 text-accent-300" />
                <span className="max-w-[14rem] truncate">{showLive ? "Генерация по брифу" : version ? versionLabel(version) : page ? `Файлы чата · ${page.name}` : "Файлы чата"}</span>
                <span className="shrink-0 text-neutral-500">{designs.length ? `· ${designs.length}` : ""}</span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0" />
              </summary>
              <div className="absolute left-0 top-full z-20 mt-2 w-72 max-w-[calc(100vw-48px)] rounded-xl border border-ink-600 bg-ink-900 p-1.5 shadow-floating">
                <button disabled={!page} onClick={() => choose({ kind: "chat" })} className={`block w-full truncate rounded-lg px-2 py-1.5 text-left text-xs disabled:opacity-50 ${!version && !showLive ? "bg-ink-800 text-white" : "hover:bg-ink-800"}`}>
                  {page ? `Файлы чата · ${page.name}` : "Файлы чата — пока пусто"}
                </button>
                <p className="px-2 pb-1 pt-2 text-[11px] uppercase text-neutral-500">Версии по брифу</p>
                {!designs.length && <p className="px-2 py-1 text-xs text-neutral-500">Пока нет — сгенерируйте по брифу.</p>}
                <div className="max-h-64 overflow-y-auto">
                  {designs.map(d => (
                    <div key={d.id} className={`flex items-center rounded-lg ${version?.id === d.id && !showLive ? "bg-ink-800 text-white" : "hover:bg-ink-800"}`}>
                      <button onClick={() => choose({ kind: "design", id: d.id })} className="min-w-0 flex-1 truncate px-2 py-1.5 text-left text-xs">{versionLabel(d)}</button>
                      <button onClick={() => removeVersion(d.id)} disabled={busy} aria-label={`Удалить ${versionLabel(d)}`} title="Удалить версию" className="shrink-0 rounded p-1.5 text-neutral-500 hover:bg-red-500/15 hover:text-red-300">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
                {designs.length > 1 && (
                  <button onClick={clearVersions} disabled={busy} className="mt-1 w-full rounded-lg px-2 py-1.5 text-left text-xs text-red-300 hover:bg-red-500/10">Удалить все версии</button>
                )}
              </div>
            </details>
            {view !== "code" && (
              <div className="ml-auto hidden gap-1 sm:flex">
                <ToolbarBtn label="Компьютер" active={bp === "desktop"} onClick={() => setBp("desktop")}><Monitor className="h-3.5 w-3.5" /></ToolbarBtn>
                <ToolbarBtn label="Планшет" active={bp === "tablet"} onClick={() => setBp("tablet")}><Tablet className="h-3.5 w-3.5" /></ToolbarBtn>
                <ToolbarBtn label="Телефон" active={bp === "mobile"} onClick={() => setBp("mobile")}><Smartphone className="h-3.5 w-3.5" /></ToolbarBtn>
              </div>
            )}
          </div>

          {showLive && liveJob.data ? (
            <div className="min-h-0 flex-1 overflow-hidden bg-ink-950/40">
              <DesignLive job={liveJob.data} view={view} width={BREAKPOINTS[bp]} stopping={stop.isPending}
                onStop={() => stop.mutate(liveJob.data!.id)}
                onClose={() => { setLiveJobId(null); setWatching(false); }} />
            </div>
          ) : (
          <div className={`min-h-0 flex-1 bg-ink-950/40 ${view === "split" && html ? "overflow-hidden" : "overflow-auto p-4"}`}>
            {!html ? (
              <div className="grid h-full place-items-center p-4 text-center">
                <div className="max-w-sm">
                  <span className="empty-orb"><Palette className="h-6 w-6" /></span>
                  <h2 className="text-xl font-semibold">Каким будет ваш следующий проект?</h2>
                  <p className="mt-3 text-sm leading-7 text-neutral-400">
                    Заполните бриф для первого макета или просто опишите задачу в чате. Здесь появится живое превью,
                    а кнопка «Открыть в Коде» перенесёт результат в домен Код — дорабатывать его до сайта.
                  </p>
                </div>
              </div>
            ) : view === "preview" ? (
              <div className="mx-auto h-full bg-white" style={{ width: BREAKPOINTS[bp], maxWidth: "100%" }}>
                {/* Изолированный sandbox: скрипты выполняются, доступа к родителю нет. */}
                <iframe title="preview" sandbox="allow-scripts" srcDoc={html} className="h-full w-full border-0" />
              </div>
            ) : view === "code" ? (
              <pre className="whitespace-pre-wrap text-xs leading-relaxed text-neutral-300"><code>{html}</code></pre>
            ) : (
              <div className="grid h-full min-h-0 grid-rows-2 lg:grid-cols-2 lg:grid-rows-1">
                <div className="min-h-0 border-b border-ink-700/60 lg:border-b-0 lg:border-r"><CodeStream code={html} live={false} /></div>
                <div className="min-h-0 p-3">
                  <div className="mx-auto h-full bg-white" style={{ width: BREAKPOINTS[bp], maxWidth: "100%" }}>
                    <iframe title="preview" sandbox="allow-scripts" srcDoc={html} className="h-full w-full border-0" />
                  </div>
                </div>
              </div>
            )}
          </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ToolbarBtn({ active, onClick, children, label }: {
  active: boolean; label?: string; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${active ? "bg-ink-700 text-white" : "text-neutral-400 hover:bg-ink-800"}`}
    >
      {children}
    </button>
  );
}
