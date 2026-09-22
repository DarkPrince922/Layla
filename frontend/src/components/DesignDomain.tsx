"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  ArrowLeft, ArrowRight, ChevronDown, Code2, Eye, Monitor, Palette, Smartphone,
  Sparkles, Tablet, Trash2,
} from "lucide-react";
import { ChatPanel } from "@/components/ChatPanel";
import { api, type Design, type FileContent, type FileNode, type Job, type ModelInfo, type Project } from "@/lib/api";

const STACKS = ["html", "react", "vue"];
const ARTIFACTS = ["Landing", "Dashboard", "Pricing", "Mobile app", "Email", "Editorial", "Slides"];
const DIRECTIONS = ["Editorial", "Modern minimal", "Tech utility", "Brutalist", "Soft warm"];
const THEMES = ["light", "dark", "both"];
const PAGES = ["Single", "Multi"];

const BREAKPOINTS = { desktop: "100%", tablet: "768px", mobile: "390px" } as const;

/** Что показываем в превью: файлы открытого чата или сохранённую версию брифа. */
type Source = { kind: "chat" } | { kind: "design"; id: string };

export function DesignDomain() {
  const qc = useQueryClient();
  const router = useRouter();
  const [brief, setBrief] = useState({
    artifact_type: "Landing",
    direction: "Modern minimal",
    tone: "",
    theme: "both",
    pages: "Single",
    reference: "",
    brand: "",
    notes: "",
  });
  const [stack, setStack] = useState("html");
  const [model, setModel] = useState("");
  const [briefOpen, setBriefOpen] = useState(false);
  const [source, setSource] = useState<Source>({ kind: "chat" });
  const [chatProject, setChatProject] = useState<string | null>(null);
  const [view, setView] = useState<"preview" | "code">("preview");
  const [bp, setBp] = useState<keyof typeof BREAKPOINTS>("desktop");
  const [pane, setPane] = useState<"chat" | "result">("chat");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // id верхней версии на момент запуска генерации: новая версия появится выше неё.
  const [awaiting, setAwaiting] = useState<string | null>(null);

  const { data: models = [] } = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelInfo[]>("/models") });
  const { data: designs = [], isSuccess: designsLoaded } = useQuery({ queryKey: ["designs"], queryFn: () => api.get<Design[]>("/designs") });

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

  const generate = useMutation({
    mutationFn: () => api.post<Job>("/designs/generate", { stack, brief, model: model || undefined }),
    onSuccess: () => {
      setAwaiting(designs[0]?.id ?? "");
      qc.invalidateQueries({ queryKey: ["designs"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setBriefOpen(false);
      setError(null);
    },
    onError: e => setError(e instanceof Error ? e.message : "Ошибка генерации"),
  });

  // Первый вход без единой версии: бриф открыт, он же и есть быстрый старт.
  useEffect(() => { if (designsLoaded && !designs.length && !chatProject) setBriefOpen(true); }, [designsLoaded, designs.length, chatProject]);
  // Фоновая генерация завершилась — сразу показываем её результат в превью.
  useEffect(() => {
    if (awaiting === null || !designs.length || designs[0].id === awaiting) return;
    setSource({ kind: "design", id: designs[0].id });
    setAwaiting(null);
  }, [awaiting, designs]);
  useEffect(() => {
    const linked = new URLSearchParams(window.location.search).get("design");
    if (linked && designs.some(d => d.id === linked)) setSource({ kind: "design", id: linked });
  }, [designs]);
  useEffect(() => {
    const mine = (e: Event) => (e as CustomEvent<{ domain?: string }>).detail?.domain === "design";
    // Клик по готовой генерации в «В работе» открывает именно эту версию.
    const result = (e: Event) => {
      if (!mine(e)) return;
      const id = (e as CustomEvent<{ design_id?: unknown }>).detail.design_id;
      if (typeof id === "string") setSource({ kind: "design", id });
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

  const picked = source.kind === "design" ? designs.find(d => d.id === source.id) || null : null;
  // Пока чат не создал страницу, показывать нечего — тогда видна последняя версия брифа.
  const version = picked || (page ? null : designs[0] || null);
  const html = version ? version.files?.[0]?.content ?? "" : live.data?.content ?? "";
  const choice = version?.id ?? "";

  async function removeVersion(id: string) {
    if (!window.confirm("Удалить эту версию макета? Проект, созданный из неё, останется.")) return;
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
              <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
                <label className="mb-1 block text-[11px] uppercase text-neutral-500">Стек</label>
                <div className="mb-3 flex gap-1">
                  {STACKS.map(s => (
                    <button key={s} onClick={() => setStack(s)} className={`flex-1 rounded px-2 py-1 text-xs ${stack === s ? "bg-accent-600 text-white" : "bg-ink-800 text-neutral-400"}`}>
                      {s === "html" ? "Plain HTML" : s === "react" ? "React" : "Vue"}
                    </button>
                  ))}
                </div>
                <Field label="Тип артефакта">
                  <select value={brief.artifact_type} onChange={e => setBrief({ ...brief, artifact_type: e.target.value })} className={INPUT}>
                    {ARTIFACTS.map(a => <option key={a}>{a}</option>)}
                  </select>
                </Field>
                <Field label="Направление">
                  <select value={brief.direction} onChange={e => setBrief({ ...brief, direction: e.target.value })} className={INPUT}>
                    {DIRECTIONS.map(d => <option key={d}>{d}</option>)}
                  </select>
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Тема">
                    <select value={brief.theme} onChange={e => setBrief({ ...brief, theme: e.target.value })} className={INPUT}>
                      {THEMES.map(t => <option key={t}>{t}</option>)}
                    </select>
                  </Field>
                  <Field label="Страницы">
                    <select value={brief.pages} onChange={e => setBrief({ ...brief, pages: e.target.value })} className={INPUT}>
                      {PAGES.map(p => <option key={p}>{p}</option>)}
                    </select>
                  </Field>
                </div>
                <Field label="Тон">
                  <input value={brief.tone} onChange={e => setBrief({ ...brief, tone: e.target.value })} placeholder="напр. дружелюбный, деловой" className={INPUT} />
                </Field>
                <Field label="Референс (URL)">
                  <input value={brief.reference} onChange={e => setBrief({ ...brief, reference: e.target.value })} className={INPUT} />
                </Field>
                <Field label="Доп. требования">
                  <textarea value={brief.notes} onChange={e => setBrief({ ...brief, notes: e.target.value })} rows={3} className={`${INPUT} resize-none`} />
                </Field>
                <Field label="Модель">
                  <select value={model} onChange={e => setModel(e.target.value)} className={INPUT}>
                    {models.length === 0 && <option value="">Нет активных моделей</option>}
                    {models.map(m => <option key={`${m.provider_id}|${m.name}`} value={m.name}>{m.name} · {m.provider}</option>)}
                  </select>
                </Field>
                <button onClick={() => generate.mutate()} disabled={generate.isPending} className="flex w-full items-center justify-center gap-1.5 rounded-md bg-accent-600 px-3 py-2 text-sm text-white hover:bg-accent-500 disabled:opacity-50">
                  <Sparkles className="h-4 w-4" />{generate.isPending ? "Отправляю…" : "Сгенерировать макет"}
                </button>
                <p className="pt-2 text-[11px] leading-5 text-neutral-500">Генерация идёт в фоне: можно уйти в другой домен, прогресс виден в панели «В работе».</p>
              </div>
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
            </div>
            <select
              aria-label="Источник превью"
              value={choice}
              onChange={e => setSource(e.target.value ? { kind: "design", id: e.target.value } : { kind: "chat" })}
              className="min-w-0 max-w-[45%] rounded-lg bg-ink-900 px-2 py-1.5 text-xs"
            >
              <option value="" disabled={!page}>{page ? `Файлы чата · ${page.name}` : "Файлы чата — пока пусто"}</option>
              {designs.map((d, i) => (
                <option key={d.id} value={d.id}>
                  {`Версия ${designs.length - i} · ${String((d.brief as Record<string, string>)?.artifact_type || "Design")} · ${d.stack}`}
                </option>
              ))}
            </select>
            {version && (
              <button onClick={() => removeVersion(version.id)} disabled={busy} aria-label="Удалить версию" title="Удалить версию" className="icon-button hover:bg-red-500/15 hover:text-red-300">
                <Trash2 className="h-4 w-4" />
              </button>
            )}
            {view === "preview" && (
              <div className="ml-auto flex gap-1">
                <ToolbarBtn label="Компьютер" active={bp === "desktop"} onClick={() => setBp("desktop")}><Monitor className="h-3.5 w-3.5" /></ToolbarBtn>
                <ToolbarBtn label="Планшет" active={bp === "tablet"} onClick={() => setBp("tablet")}><Tablet className="h-3.5 w-3.5" /></ToolbarBtn>
                <ToolbarBtn label="Телефон" active={bp === "mobile"} onClick={() => setBp("mobile")}><Smartphone className="h-3.5 w-3.5" /></ToolbarBtn>
              </div>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-auto bg-ink-950/40 p-4">
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
            ) : (
              <pre className="whitespace-pre-wrap text-xs leading-relaxed text-neutral-300"><code>{html}</code></pre>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const INPUT = "w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <label className="mb-1 block text-[11px] uppercase text-neutral-500">{label}</label>
      {children}
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
