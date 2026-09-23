"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, Bot, Plus, Loader2, Download, Square, Trash2, Eraser, Zap, ShieldCheck, ClipboardList, ChevronDown, Pencil, Check, Search, FoldVertical, Undo2, Play, SearchCheck, BookMarked, ListChecks, CircleCheck, CircleDot, Circle, X, type LucideIcon } from "lucide-react";
import { ApiError, api, downloadProject, type FileContent, type Persona, type ModelInfo, type Chat, type ChatDetail, type FileChange, type Job, type ToolEvent } from "@/lib/api";
import { useAuth } from "@/store/auth";
import { FileDiff } from "@/components/FileDiff";
import { confirmAction } from "@/components/ConfirmDialog";

const active = (job?: Job | null) => !!job && ["queued", "running"].includes(job.status);

type Mode = "auto" | "confirm" | "plan" | "review";
type Decision = "approve" | "reject" | "approve_all";
// Режимы как в IDE-агентах: сам применяет / спрашивает перед каждым изменением / только план.
const MODES: { id: Mode; label: string; hint: string; icon: LucideIcon }[] = [
  { id: "auto", label: "Авто", hint: "Агент сам применяет изменения", icon: Zap },
  { id: "confirm", label: "С подтверждением", hint: "Каждое изменение файла ждёт вашего «Применить»", icon: ShieldCheck },
  { id: "plan", label: "План", hint: "Только чтение: агент изучит задачу и предложит план, ничего не меняя", icon: ClipboardList },
  { id: "review", label: "Ревью", hint: "Только чтение: агент проверит код и перечислит найденные проблемы по важности", icon: SearchCheck },
];
const isMode = (value: string | null): value is Mode => MODES.some(m => m.id === value);
const read = (key: string) => { try { return localStorage.getItem(key); } catch { return null; } };
const remember = (key: string, value: string) => { try { localStorage.setItem(key, value); } catch { /* Storage can be unavailable. */ } };

// crypto.randomUUID есть только в защищённом контексте (HTTPS или localhost).
// По обычному HTTP его нет, поэтому собираем UUID v4 из getRandomValues,
// который доступен всегда; последний фолбэк — на случай совсем старых браузеров.
const newRequestId = (): string => {
  const source = globalThis.crypto;
  if (typeof source?.randomUUID === "function") return source.randomUUID();
  if (typeof source?.getRandomValues === "function") {
    const bytes = source.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 14)}`;
};

export function ChatPanel({ domain, projectId, onFileChange, onOpenFile, onProject }: {
  domain: string; projectId?: string; onFileChange?: (change: FileChange) => void; onOpenFile?: (path: string) => void;
  onProject?: (projectId: string | null) => void;
}) {
  const owner = useAuth(s => s.user?.id);
  const qc = useQueryClient();
  const scope = `layla:chat:${owner}:${domain}:${projectId || ""}`;
  const [chatId, setChatId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [input, setInput] = useState("");
  const [personaId, setPersonaId] = useState("");
  const [model, setModel] = useState("");
  const [providerId, setProviderId] = useState("");
  const [sending, setSending] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const modeKey = `layla:mode:${owner}:${domain}`;
  const [mode, setMode] = useState<Mode>("auto");
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  const generation = useRef(0);
  const request = useRef<{ content: string; chat: string; id: string } | null>(null);
  const applied = useRef(new Set<string>());
  const scroll = useRef<HTMLDivElement>(null);
  const { data: personas = [] } = useQuery({ queryKey: ["personas"], queryFn: () => api.get<Persona[]>("/personas") });
  const { data: models = [] } = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelInfo[]>("/models") });
  const history = useQuery({ queryKey: ["chats", owner, domain, projectId], queryFn: () => api.get<Chat[]>(`/chats?domain=${domain}${projectId ? `&project_id=${projectId}` : ""}`) });
  // Поиск по истории: по названию и тексту сообщений (на сервере), с небольшой задержкой.
  const historyMenu = useRef<HTMLDetailsElement>(null);
  const [query, setQuery] = useState("");
  const [needle, setNeedle] = useState("");
  useEffect(() => { const t = setTimeout(() => setNeedle(query.trim()), 300); return () => clearTimeout(t); }, [query]);
  const found = useQuery({
    queryKey: ["chats", owner, domain, projectId, "search", needle],
    enabled: !!needle,
    queryFn: () => api.get<Chat[]>(`/chats?domain=${domain}${projectId ? `&project_id=${projectId}` : ""}&q=${encodeURIComponent(needle)}`),
  });
  const listed = needle ? found.data || [] : history.data || [];
  const [renaming, setRenaming] = useState<string | null>(null);
  const detail = useQuery({ queryKey: ["chat", owner, chatId], enabled: !!chatId,
    queryFn: () => api.get<ChatDetail>(`/chats/${chatId}`),
    refetchInterval: q => active(q.state.data?.last_job) ? 1000 : false,
    refetchIntervalInBackground: true,
  });
  const job = detail.data?.last_job;
  const running = active(job);
  // Пока история чата грузится, неизвестно, занят ли он: отправка подождёт.
  const loading = !!chatId && detail.isPending;
  const approval = (running ? job?.result?.approval : null) as { id: string } | null | undefined;
  const messages = useMemo(() => detail.data?.messages || [], [detail.data?.messages]);

  function select(id: string | null) {
    generation.current++;
    setChatId(id); setError(null); setPersonaId("");
    remember(scope, id || "new");
    setInput(read(`${scope}:draft:${id || "new"}`) || "");
  }
  useEffect(() => {
    setReady(false); setChatId(null); generation.current++;
    // This counter invalidates pending async work when leaving the scope.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => { generation.current++; };
  }, [scope]);
  useEffect(() => {
    if (ready || !history.data) return;
    const linked = new URLSearchParams(window.location.search).get("chat");
    const stored = read(scope);
    const id = history.data.find(c => c.id === linked)?.id || (stored === "new" ? null : history.data.find(c => c.id === stored)?.id || history.data[0]?.id || null);
    select(id); setReady(true);
    // Selection is initialized once; history refresh must not reopen another chat.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history.data, ready, scope]);
  useEffect(() => {
    const open = (event: Event) => {
      const target = (event as CustomEvent<Chat>).detail;
      if (target.domain === domain && (!projectId || target.project_id === projectId)) select(target.id);
    };
    window.addEventListener("layla:open-chat", open);
    return () => window.removeEventListener("layla:open-chat", open);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain, projectId, scope]);
  const chatProject = detail.data?.project_id ?? null;
  // Каждый чат работает в своём каталоге на диске: сообщаем его наружу,
  // чтобы домен мог показать результат (превью) и открыть тот же проект в «Коде».
  useEffect(() => { onProject?.(chatId ? chatProject : null); }, [chatId, chatProject, onProject]);
  const savedPersona = detail.data?.persona_id;
  const savedModel = detail.data?.model;
  const savedProvider = detail.data?.provider_id;
  const savedId = detail.data?.id;
  useEffect(() => {
    if (savedId) {
      setPersonaId(savedPersona || "");
      if (savedModel) setModel(savedModel);
      // Провайдер тоже из чата: у разных провайдеров модели могут называться одинаково.
      if (savedProvider) setProviderId(savedProvider);
    }
  }, [savedId, savedModel, savedPersona, savedProvider]);
  useEffect(() => { if (!model && models.length) { setModel(models[0].name); setProviderId(models[0].provider_id); } }, [model, models]);
  // Имя модели может встречаться у нескольких провайдеров, поэтому пикер хранит
  // пару «провайдер+модель». Если провайдер неизвестен (модель восстановлена из
  // чата) — берём первого, у кого эта модель есть.
  const selected = useMemo(
    () => models.find(m => m.name === model && m.provider_id === providerId) || models.find(m => m.name === model) || null,
    [models, model, providerId],
  );
  const choice = selected ? `${selected.provider_id}|${selected.name}` : "";
  function pickModel(value: string) {
    const at = value.indexOf("|");
    if (at < 0) return;
    setProviderId(value.slice(0, at));
    setModel(value.slice(at + 1));
  }
  useEffect(() => { const saved = read(modeKey); if (isMode(saved)) setMode(saved); }, [modeKey]);
  function pickMode(next: Mode) { setMode(next); remember(modeKey, next); }
  // Выбор роли подставляет её умолчания (режим и модель), если они заданы в настройках.
  function pickPersona(id: string) {
    setPersonaId(id);
    const persona = personas.find(p => p.id === id);
    if (persona?.default_mode) pickMode(persona.default_mode);
    const preferred = persona?.default_model ? models.find(m => m.name === persona.default_model) : undefined;
    if (preferred) { setModel(preferred.name); setProviderId(preferred.provider_id); }
  }
  useEffect(() => {
    for (const message of messages) for (const tool of message.meta?.tools || []) {
      const key = `${message.id}:${tool.id}`;
      // Превью на подтверждение — ещё не изменение: сообщаем только о применённых.
      if (tool.change && tool.status === "done" && !applied.current.has(key)) { applied.current.add(key); onFileChange?.(tool.change); }
    }
    if (scroll.current && scroll.current.scrollHeight - scroll.current.scrollTop - scroll.current.clientHeight < 300) scroll.current.scrollTo({ top: scroll.current.scrollHeight });
  }, [messages, onFileChange]);

  async function send(override?: { text: string; mode: Mode }) {
    const content = (override?.text ?? input).trim();
    const runMode = override?.mode ?? mode;
    if (!content || running || loading || !ready || lock.current) return;
    if (!model) { setError("Добавьте активную модель в настройках провайдеров."); return; }
    lock.current = true; setSending(true); setError(null);
    const version = generation.current;
    let id = chatId;
    try {
      if (!id) {
        const chat = await api.post<Chat>("/chats", { domain, project_id: projectId || null, title: content.slice(0, 80), persona_id: personaId || null, model, provider_id: selected?.provider_id || null });
        id = chat.id;
        if (version === generation.current) { setChatId(id); remember(scope, id); }
      }
      if (!request.current || request.current.content !== content || request.current.chat !== id) request.current = { content, chat: id, id: newRequestId() };
      await api.post<Job>(`/chats/${id}/run`, { content, model, provider_id: selected?.provider_id, request_id: request.current.id, mode: runMode });
      if (!override) {
        remember(`${scope}:draft:${chatId || "new"}`, "");
        if (version === generation.current) setInput("");
      }
      request.current = null;
    } catch (e) {
      if (version === generation.current) setError(e instanceof Error ? e.message : "Не удалось отправить задачу");
    } finally {
      await Promise.all([qc.invalidateQueries({ queryKey: ["chat", owner, id] }), qc.invalidateQueries({ queryKey: ["chats"] }), qc.invalidateQueries({ queryKey: ["jobs"] }), qc.invalidateQueries({ queryKey: ["projects"] })]);
      lock.current = false; setSending(false);
    }
  }
  async function stop() {
    if (!job) return;
    try { await api.post(`/jobs/${job.id}/cancel`, {}); await qc.invalidateQueries({ queryKey: ["chat", owner, chatId] }); await qc.invalidateQueries({ queryKey: ["jobs"] }); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось остановить задачу"); }
  }
  async function decide(decision: Decision) {
    if (!job || !approval || deciding) return;
    setDeciding(true); setError(null);
    try {
      await api.post(`/jobs/${job.id}/decision`, { approval_id: approval.id, decision });
      await qc.invalidateQueries({ queryKey: ["chat", owner, chatId] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось передать решение");
      await qc.invalidateQueries({ queryKey: ["chat", owner, chatId] });
    } finally { setDeciding(false); }
  }
  function pick(id: string | null) {
    if (sending || !ready) return;
    select(id);
    setQuery("");
    if (historyMenu.current) historyMenu.current.open = false;
  }
  async function saveTitle() {
    const title = (renaming || "").trim();
    if (!chatId || !title) { setRenaming(null); return; }
    try {
      await api.patch(`/chats/${chatId}`, { title });
      await Promise.all([qc.invalidateQueries({ queryKey: ["chats"] }), qc.invalidateQueries({ queryKey: ["chat", owner, chatId] })]);
      setRenaming(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось переименовать чат");
    }
  }
  async function zip() {
    if (!detail.data?.project_id) return;
    try { await downloadProject({ id: detail.data.project_id, name: detail.data.title || "Layla" }); }
    catch (e) { setError(e instanceof Error ? e.message : "Не удалось скачать файлы"); }
  }
  // Удаление доступно во всех доменах. Активную задачу бэкенд не даст удалить
  // (409) — сначала остановите её, иначе фоновая работа осталась бы без чата.
  async function removeChat() {
    if (!chatId || removing) return;
    const title = history.data?.find(c => c.id === chatId)?.title || "Чат";
    const files = domain === "code" ? "" : " Вместе с ним — файлы, созданные в этом чате.";
    if (!(await confirmAction(`Удалить «${title}»?${files}\nЧат попадёт в корзину: 7 дней его можно восстановить (Настройки → Корзина).`))) return;
    setRemoving(true); setError(null);
    try {
      await api.del(`/chats/${chatId}`);
      remember(`${scope}:draft:${chatId}`, "");
      const rest = (history.data || []).filter(c => c.id !== chatId);
      select(rest[0]?.id || null);
      await history.refetch();
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить чат");
    } finally { setRemoving(false); }
  }
  async function clearHistory() {
    const count = history.data?.length || 0;
    if (!count || removing) return;
    const files = domain === "code" ? "" : " Вместе с ними — их файлы.";
    if (!(await confirmAction(`Удалить всю историю раздела — ${count} чат(ов)?${files}\nЧаты попадут в корзину: 7 дней их можно восстановить (Настройки → Корзина).`, "Очистить"))) return;
    setRemoving(true); setError(null);
    try {
      const params = new URLSearchParams({ domain });
      if (projectId) params.set("project_id", projectId);
      const result = await api.del<{ deleted: number; skipped: number }>(`/chats?${params}`);
      for (const c of history.data || []) remember(`${scope}:draft:${c.id}`, "");
      select(null);
      await history.refetch();
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["projects"] });
      if (result?.skipped) setError(`Не удалено чатов с работающей задачей: ${result.skipped}. Остановите их и повторите.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось очистить историю");
    } finally { setRemoving(false); }
  }
  /** «Откатить к этой точке»: файлы — как до этого ответа (и всех следующих). */
  async function rollback(messageId: string) {
    if (!chatId || running) return;
    if (!(await confirmAction("Вернуть файлы к состоянию до этого ответа? Изменения этого и всех следующих ответов агента будут отменены. Правки, сделанные вручную после них, тоже перезапишутся.", "Откатить"))) return;
    setError(null);
    try {
      const result = await api.post<{ restored: string[]; messages: number }>(`/chats/${chatId}/messages/${messageId}/rollback`);
      for (const path of result.restored) onFileChange?.({ path, operation: "edit", diff: "", before_sha256: null, after_sha256: null });
      await qc.invalidateQueries({ queryKey: ["chat", owner, chatId] });
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось откатить изменения"); }
  }
  /** «Сжать контекст»: старые сообщения — в сводку, свежие остаются как есть. */
  async function compact() {
    if (!chatId || running || sending) return;
    setError(null);
    try {
      await api.post<Job>(`/chats/${chatId}/compact`);
      await qc.invalidateQueries({ queryKey: ["chat", owner, chatId] });
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось сжать контекст"); }
  }
  const last = messages.at(-1);
  // Ход прерван (ошибка, остановка, лимит шагов или длины) — можно продолжить с того же места.
  const interrupted = !running && !sending && last?.role === "assistant" && (!!last.meta?.error || /«продолжай»/.test(last.content.slice(-200)));
  const planReady = !running && !sending && last?.role === "assistant" && last.meta?.mode === "plan" && !last.meta?.error && !!last.content.trim();
  const reviewReady = !running && !sending && last?.role === "assistant" && last.meta?.mode === "review" && !last.meta?.error && !!last.content.trim();
  const [rulesOpen, setRulesOpen] = useState(false);
  return <div className="chat-panel flex h-full min-h-0 min-w-0 flex-col">
    <div className="relative flex items-center gap-2 border-b border-ink-700/50 px-4 py-3">
      <Bot className="hidden h-5 w-5 shrink-0 text-accent-300 sm:block" />
      {renaming !== null ? (
        <form className="flex min-w-0 flex-1 items-center gap-1" onSubmit={e => { e.preventDefault(); saveTitle(); }}>
          <input autoFocus aria-label="Название чата" value={renaming} maxLength={300} onChange={e => setRenaming(e.target.value)}
            onKeyDown={e => { if (e.key === "Escape") setRenaming(null); }} className="min-w-0 flex-1 rounded-lg bg-ink-900 p-2 text-sm" />
          <button aria-label="Сохранить название" className="icon-button"><Check className="h-4 w-4" /></button>
        </form>
      ) : (
        <details ref={historyMenu} className="min-w-0 flex-1">
          <summary aria-label="История чатов" className="flex cursor-pointer list-none items-center gap-2 rounded-lg bg-ink-900 p-2 text-sm [&::-webkit-details-marker]:hidden">
            <span className="min-w-0 flex-1 truncate">{chatId ? detail.data?.title || history.data?.find(c => c.id === chatId)?.title || "Чат" : "Новый чат"}</span>
            <ChevronDown className="h-4 w-4 shrink-0 text-neutral-500" />
          </summary>
          {/* Меню во всю ширину шапки чата: на телефоне не вылезает за край. */}
          <div className="absolute inset-x-3 top-full z-30 mt-1 rounded-xl border border-ink-600 bg-ink-900 p-2 shadow-floating">
            <label className="mb-2 flex items-center gap-2 rounded-lg bg-ink-800 px-2">
              <Search className="h-4 w-4 shrink-0 text-neutral-500" />
              <input aria-label="Поиск по чатам" value={query} onChange={e => setQuery(e.target.value)} placeholder="Поиск по названию и тексту…"
                className="min-w-0 flex-1 bg-transparent py-2 text-sm outline-none" />
              {needle && found.isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-neutral-500" />}
            </label>
            {!needle && <button onClick={() => pick(null)} className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm ${!chatId ? "bg-ink-800 text-white" : "text-neutral-300 hover:bg-ink-800"}`}><Plus className="h-4 w-4" />Новый чат</button>}
            <div className="max-h-72 overflow-y-auto">
              {listed.map(c => <button key={c.id} onClick={() => pick(c.id)} className={`block w-full truncate rounded-lg px-2 py-1.5 text-left text-sm ${c.id === chatId ? "bg-ink-800 text-white" : "text-neutral-300 hover:bg-ink-800"}`}>{c.title || "Чат"}</button>)}
              {needle && !found.isFetching && !listed.length && <p className="px-2 py-3 text-xs text-neutral-500">Ничего не найдено.</p>}
            </div>
            {!needle && !!history.data?.length && (
              <button onClick={() => { if (historyMenu.current) historyMenu.current.open = false; clearHistory(); }} disabled={sending || removing || !ready}
                className="mt-1 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-red-300 hover:bg-red-500/10">
                <Eraser className="h-3.5 w-3.5" />Очистить историю раздела
              </button>
            )}
          </div>
        </details>
      )}
      {chatId && messages.length > 6 && <button aria-label="Сжать контекст" title="Сжать контекст: старые сообщения — в сводку для модели, чтобы разговор мог продолжаться без ограничений" onClick={compact} disabled={running || sending} className="icon-button"><FoldVertical className="h-4 w-4" /></button>}
      {detail.data?.project_id && <button aria-label="Правила проекта" title="Правила проекта (LAYLA.md): модель видит их в каждом ответе" onClick={() => setRulesOpen(true)} className="icon-button"><BookMarked className="h-4 w-4" /></button>}
      {chatId && renaming === null && <button aria-label="Переименовать чат" title="Переименовать" onClick={() => setRenaming(detail.data?.title || "")} className="icon-button"><Pencil className="h-4 w-4" /></button>}
      {detail.data?.project_id && <button onClick={zip} className="icon-button" aria-label="Скачать файлы чата ZIP"><Download className="h-4 w-4" /></button>}
      {chatId && <button aria-label="Удалить чат" title="Удалить чат" onClick={removeChat} disabled={sending || removing || !ready} className="icon-button hover:bg-red-500/15 hover:text-red-300">{removing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}</button>}
      <button aria-label="Новый чат" onClick={() => select(null)} disabled={sending || !ready} className="icon-button"><Plus className="h-5 w-5" /></button>
    </div>
    <div ref={scroll} className="chat-scroll min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
      {history.isError || detail.isError ? <p role="alert" className="text-red-300">Не удалось загрузить историю. <button onClick={() => { history.refetch(); detail.refetch(); }} className="underline">Повторить</button></p> : !ready || (chatId && detail.isPending) ? <p className="text-sm text-neutral-500">Загрузка истории…</p> : !messages.length && <div className="mx-auto my-12 max-w-sm text-center"><div className="empty-orb mx-auto mb-5"><Bot className="h-7 w-7" /></div><h2 className="text-xl font-semibold">Что сделаем сегодня?</h2><p className="mt-3 text-sm leading-relaxed text-neutral-400">Опишите задачу. Можно переключаться между разделами и создавать новые чаты — работа продолжится, история и файлы сохранятся.</p></div>}
      {messages.map(m => m.meta?.kind === "summary" ? <details key={m.id} className="rounded-xl border border-dashed border-ink-600 px-4 py-2 text-xs text-neutral-400">
        <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden"><FoldVertical className="h-3.5 w-3.5 shrink-0 text-accent-300" /><span className="flex-1">Контекст сжат · {m.meta.count || "старые"} сообщений выше модель видит как сводку</span><ChevronDown className="h-3.5 w-3.5" /></summary>
        <p className="mt-2 whitespace-pre-wrap break-words leading-relaxed">{m.content}</p>
      </details> : <article key={m.id} className={`min-w-0 ${m.role === "user" ? "ml-auto max-w-[90%] rounded-2xl bg-accent-500/15 p-4" : "rounded-2xl bg-ink-800/40 p-4"}`}>
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-neutral-500">
          <span className="flex-1">{m.role === "user" ? "Вы" : "Layla"}</span>
          {m.meta?.rolled_back && <span className="rounded-full bg-amber-500/10 px-2 py-0.5 font-normal text-amber-200">Изменения откатены</span>}
          {m.role === "assistant" && m.meta?.checkpoint && !m.meta.rolled_back && !running && <button onClick={() => rollback(m.id)} title="Вернуть файлы к состоянию до этого ответа" className="flex items-center gap-1 rounded-md px-1.5 py-0.5 font-normal text-neutral-500 hover:bg-ink-700 hover:text-neutral-200"><Undo2 className="h-3.5 w-3.5" />Откатить к этой точке</button>}
        </div>
        {m.meta?.reasoning && <details className="mb-3 rounded-lg border border-ink-700 p-3"><summary className="cursor-pointer text-xs text-neutral-400">Размышление</summary><p className="mt-3 whitespace-pre-wrap break-words text-xs leading-relaxed text-neutral-400">{m.meta.reasoning}</p></details>}
        {!!m.meta?.todos?.length && <Todos items={m.meta.todos} />}
        <div className="whitespace-pre-wrap break-words text-sm leading-7">{m.content}</div>
        {m.meta?.tools?.map(tool => <div key={tool.id} className="mt-3">{tool.status === "pending" ? <Approval tool={tool} active={approval?.id === tool.id} busy={deciding} onDecide={decide} /> : tool.status === "rejected" ? <p className="rounded-lg border border-ink-700 p-3 text-xs text-neutral-400">Отклонено вами · <span className="break-all font-mono">{tool.path}</span></p> : tool.change && tool.status === "done" ? <FileDiff change={tool.change} onOpen={onOpenFile} /> : <div className={`rounded-lg border p-3 text-xs ${tool.status === "error" ? "border-red-500/30 text-red-300" : "border-ink-700 text-neutral-400"}`}><span>{tool.status === "running" ? "Выполняется" : tool.status === "done" ? "Готово" : "Ошибка"} · {tool.name}</span><p className="mt-1 break-all font-mono">{tool.path}</p>{tool.error && <p>{tool.error}</p>}</div>}</div>)}
        {m.meta?.error && <p role="alert" className="mt-3 text-sm text-red-300">{m.meta.error}</p>}
      </article>)}
    </div>
    {error && <p role="alert" className="mx-5 mb-3 text-sm text-red-300">{error}</p>}
    {job && <div role="status" className="mx-5 mb-3 flex items-center gap-2 text-xs text-neutral-400">{running && <Loader2 className="h-4 w-4 animate-spin" />}<span className="min-w-0 flex-1 break-words">{running ? job.steps.at(-1)?.text || "Задача в очереди" : job.status === "error" ? job.error || "Ошибка выполнения" : job.status === "cancelled" ? "Остановлено" : "Готово"}</span>{running && <button onClick={stop} className="icon-button" aria-label="Остановить задачу"><Square className="h-3 w-3" /></button>}</div>}
    {interrupted && <div className="mx-5 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-amber-400/30 bg-amber-500/5 p-3 text-xs">
      <Play className="h-4 w-4 shrink-0 text-amber-200" /><span className="min-w-0 flex-1">Ход прерван. Уже сделанное сохранено — агент продолжит с того же места.</span>
      <button className="primary-button !px-3 !py-1.5 text-xs" onClick={() => send({ text: "Продолжи с того места, где остановился.", mode })}>Продолжить</button>
    </div>}
    {reviewReady && <div className="mx-5 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-accent-500/30 bg-accent-500/10 p-3 text-xs">
      <SearchCheck className="h-4 w-4 shrink-0 text-accent-300" /><span className="min-w-0 flex-1">Ревью готово. Исправить найденное?</span>
      <button className="primary-button !px-3 !py-1.5 text-xs" onClick={() => { pickMode("auto"); send({ text: "Исправь найденные проблемы, начиная с самых важных.", mode: "auto" }); }}>Исправить</button>
      <button className="secondary-button !px-3 !py-1.5 text-xs" onClick={() => { pickMode("confirm"); send({ text: "Исправь найденные проблемы, начиная с самых важных.", mode: "confirm" }); }}>С подтверждением</button>
    </div>}
    {rulesOpen && detail.data?.project_id && <RulesDialog projectId={detail.data.project_id} onClose={() => setRulesOpen(false)} />}
    {planReady && <div className="mx-5 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-accent-500/30 bg-accent-500/10 p-3 text-xs">
      <ClipboardList className="h-4 w-4 shrink-0 text-accent-300" /><span className="min-w-0 flex-1">План готов. Выполнить?</span>
      <button className="primary-button !px-3 !py-1.5 text-xs" onClick={() => { pickMode("auto"); send({ text: "Выполни этот план.", mode: "auto" }); }}>Выполнить</button>
      <button className="secondary-button !px-3 !py-1.5 text-xs" onClick={() => { pickMode("confirm"); send({ text: "Выполни этот план.", mode: "confirm" }); }}>С подтверждением</button>
    </div>}
    <div className="mx-4 mb-4 rounded-2xl border border-ink-600/60 bg-ink-800/70 p-3">
      <div role="radiogroup" aria-label="Режим агента" className="mb-2 flex flex-wrap gap-1 text-xs">
        {MODES.map(({ id, label, hint, icon: Icon }) => <button key={id} role="radio" aria-checked={mode === id} title={hint} onClick={() => pickMode(id)} className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 ${mode === id ? "bg-accent-500/20 text-accent-100" : "text-neutral-400 hover:bg-ink-900"}`}><Icon className="h-3.5 w-3.5" />{label}</button>)}
      </div>
      <div className="mb-3 flex flex-wrap gap-2 text-xs">
        <select aria-label="Персона" value={personaId} disabled={!!chatId || sending} onChange={e => pickPersona(e.target.value)} className="min-w-0 max-w-full rounded-lg bg-ink-900 px-3 py-2"><option value="">Без персоны</option>{personas.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        <select aria-label="Модель" value={choice} disabled={running || sending} onChange={e => pickModel(e.target.value)} className="min-w-0 max-w-full rounded-lg bg-ink-900 px-3 py-2">{!models.length && <option value="">Нет активных моделей</option>}{models.map(m => <option key={`${m.provider_id}|${m.name}`} value={`${m.provider_id}|${m.name}`}>{m.name} · {m.provider}</option>)}</select>
      </div>
      <div className="flex items-end gap-3"><textarea aria-label="Сообщение агенту" rows={3} value={input} onChange={e => { setInput(e.target.value); remember(`${scope}:draft:${chatId || "new"}`, e.target.value); }} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} placeholder={running ? "Задача выполняется. Для другой задачи создайте новый чат." : mode === "plan" ? "Опишите задачу — агент предложит план…" : "Опишите задачу…"} className="min-w-0 flex-1 resize-none bg-transparent p-2 text-sm outline-none" /><button aria-label="Отправить" onClick={() => send()} disabled={!input.trim() || running || sending || loading || !ready} className="primary-button h-11 w-11 shrink-0 !p-0">{sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}</button></div>
      <p className="px-2 pt-2 text-[11px] text-neutral-500">{MODES.find(m => m.id === mode)?.hint}. Работа продолжается в фоне.</p>
    </div>
  </div>;
}

/** План агента (update_todos): что сделано, что в работе, что впереди. */
function Todos({ items }: { items: { content: string; status: "pending" | "in_progress" | "done" }[] }) {
  const done = items.filter(t => t.status === "done").length;
  return <div className="mb-3 rounded-lg border border-ink-700 p-3">
    <p className="mb-2 flex items-center gap-1.5 text-xs text-neutral-400"><ListChecks className="h-3.5 w-3.5 text-accent-300" />План · {done} из {items.length}</p>
    <ul className="space-y-1 text-xs">
      {items.map((t, i) => <li key={i} className={`flex items-start gap-2 ${t.status === "done" ? "text-neutral-500 line-through" : t.status === "in_progress" ? "text-neutral-100" : "text-neutral-400"}`}>
        {t.status === "done" ? <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" /> : t.status === "in_progress" ? <CircleDot className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent-300" /> : <Circle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
        <span className="min-w-0 break-words">{t.content}</span>
      </li>)}
    </ul>
  </div>;
}

/** Правила проекта — файл LAYLA.md: модель видит их в каждом ответе этого проекта. */
function RulesDialog({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [text, setText] = useState("");
  const [sha, setSha] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "saving">("loading");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.get<FileContent>(`/projects/${projectId}/file?path=LAYLA.md`)
      .then(file => { if (alive) { setText(file.content); setSha(file.sha256); setState("ready"); } })
      .catch(e => { if (alive) { if (!(e instanceof ApiError && e.status === 404)) setMsg(e instanceof Error ? e.message : "Не удалось прочитать"); setState("ready"); } });
    return () => { alive = false; };
  }, [projectId]);
  async function save() {
    setState("saving"); setMsg(null);
    try {
      const change = await api.put<{ after_sha256: string | null }>(`/projects/${projectId}/file`, { path: "LAYLA.md", content: text, expected_sha256: sha });
      setSha(change.after_sha256);
      setMsg("Сохранено — правила действуют со следующего ответа");
    } catch (e) { setMsg(e instanceof Error ? e.message : "Не удалось сохранить"); }
    finally { setState("ready"); }
  }
  // Портал в body: иначе «fixed» ограничен колонкой чата (у неё свой контейнер).
  return createPortal(<div role="dialog" aria-label="Правила проекта" className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={onClose}>
    <div className="w-full max-w-lg rounded-2xl border border-ink-600 bg-ink-900 p-4 shadow-floating" onClick={e => e.stopPropagation()}>
      <div className="mb-2 flex items-center gap-2"><BookMarked className="h-4 w-4 text-accent-300" /><h2 className="flex-1 text-sm font-semibold">Правила проекта · LAYLA.md</h2><button onClick={onClose} aria-label="Закрыть" className="icon-button !h-8 !w-8"><X className="h-4 w-4" /></button></div>
      <p className="mb-3 text-xs leading-5 text-neutral-400">Постоянные указания для модели в этом проекте: стек, стиль, что можно и нельзя. Модель видит их в каждом ответе и сама дописывает сюда то, что вы просите запомнить.</p>
      <textarea aria-label="Текст правил" value={text} onChange={e => setText(e.target.value)} rows={10} disabled={state === "loading"}
        placeholder={"- Стек: HTML + Tailwind\n- Тексты на «вы», без англицизмов\n- Не трогать папку legacy/"}
        className="w-full resize-y rounded-lg border border-ink-700 bg-ink-800 p-3 font-mono text-xs leading-5" />
      {msg && <p role="status" className="mt-2 text-xs text-neutral-300">{msg}</p>}
      <div className="mt-3 flex justify-end gap-2">
        <button onClick={onClose} className="secondary-button !px-3 !py-1.5 text-xs">Закрыть</button>
        <button onClick={save} disabled={state !== "ready"} className="primary-button !px-3 !py-1.5 text-xs">{state === "saving" ? "Сохраняю…" : "Сохранить"}</button>
      </div>
    </div>
  </div>, document.body);
}

/** Изменение, ждущее решения пользователя (режим «С подтверждением»). */
function Approval({ tool, active, busy, onDecide }: {
  tool: ToolEvent; active: boolean; busy: boolean; onDecide: (decision: Decision) => void;
}) {
  return <div className="rounded-xl border border-amber-400/40 bg-amber-500/5 p-2">
    <p className="px-2 pt-1 text-xs font-medium text-amber-200">{active ? "Применить это изменение?" : "Ожидало подтверждения"}</p>
    {tool.change ? <FileDiff change={tool.change} expanded={active} /> : <p className="p-2 break-all font-mono text-xs text-neutral-400">{tool.name} · {tool.path}</p>}
    {active && <div className="flex flex-wrap gap-2 px-2 pb-1">
      <button className="primary-button !px-3 !py-1.5 text-xs" disabled={busy} onClick={() => onDecide("approve")}>Применить</button>
      <button className="secondary-button !px-3 !py-1.5 text-xs" disabled={busy} onClick={() => onDecide("reject")}>Отклонить</button>
      <button className="secondary-button !px-3 !py-1.5 text-xs" disabled={busy} onClick={() => onDecide("approve_all")} title="Остальные изменения этого ответа применятся без вопросов">Применять всё</button>
    </div>}
  </div>;
}
