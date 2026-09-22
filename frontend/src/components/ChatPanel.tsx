"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, Bot, Plus, Loader2, Download, Square, Trash2 } from "lucide-react";
import { api, downloadProject, type Persona, type ModelInfo, type Chat, type ChatDetail, type FileChange, type Job } from "@/lib/api";
import { useAuth } from "@/store/auth";
import { FileDiff } from "@/components/FileDiff";

const active = (job?: Job | null) => !!job && ["queued", "running"].includes(job.status);
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
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  const generation = useRef(0);
  const request = useRef<{ content: string; chat: string; id: string } | null>(null);
  const applied = useRef(new Set<string>());
  const scroll = useRef<HTMLDivElement>(null);
  const { data: personas = [] } = useQuery({ queryKey: ["personas"], queryFn: () => api.get<Persona[]>("/personas") });
  const { data: models = [] } = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelInfo[]>("/models") });
  const history = useQuery({ queryKey: ["chats", owner, domain, projectId], queryFn: () => api.get<Chat[]>(`/chats?domain=${domain}${projectId ? `&project_id=${projectId}` : ""}`) });
  const detail = useQuery({ queryKey: ["chat", owner, chatId], enabled: !!chatId,
    queryFn: () => api.get<ChatDetail>(`/chats/${chatId}`),
    refetchInterval: q => active(q.state.data?.last_job) ? 1000 : false,
    refetchIntervalInBackground: true,
  });
  const job = detail.data?.last_job;
  const running = active(job);
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
  const savedId = detail.data?.id;
  useEffect(() => {
    if (savedId) { setPersonaId(savedPersona || ""); if (savedModel) setModel(savedModel); }
  }, [savedId, savedModel, savedPersona]);
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
  useEffect(() => {
    for (const message of messages) for (const tool of message.meta?.tools || []) {
      const key = `${message.id}:${tool.id}`;
      if (tool.change && !applied.current.has(key)) { applied.current.add(key); onFileChange?.(tool.change); }
    }
    if (scroll.current && scroll.current.scrollHeight - scroll.current.scrollTop - scroll.current.clientHeight < 300) scroll.current.scrollTo({ top: scroll.current.scrollHeight });
  }, [messages, onFileChange]);

  async function send() {
    const content = input.trim();
    if (!content || running || !ready || lock.current) return;
    if (!model) { setError("Добавьте активную модель в настройках провайдеров."); return; }
    lock.current = true; setSending(true); setError(null);
    const version = generation.current;
    let id = chatId;
    try {
      if (!id) {
        const chat = await api.post<Chat>("/chats", { domain, project_id: projectId || null, title: content.slice(0, 80), persona_id: personaId || null, model });
        id = chat.id;
        if (version === generation.current) { setChatId(id); remember(scope, id); }
      }
      if (!request.current || request.current.content !== content || request.current.chat !== id) request.current = { content, chat: id, id: newRequestId() };
      await api.post<Job>(`/chats/${id}/run`, { content, model, provider_id: selected?.provider_id, request_id: request.current.id });
      remember(`${scope}:draft:${chatId || "new"}`, "");
      if (version === generation.current) setInput("");
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
    if (!confirm(`Удалить «${title}»? Сообщения и история задач этого чата будут удалены безвозвратно.`)) return;
    setRemoving(true); setError(null);
    try {
      await api.del(`/chats/${chatId}`);
      remember(`${scope}:draft:${chatId}`, "");
      const rest = (history.data || []).filter(c => c.id !== chatId);
      select(rest[0]?.id || null);
      await history.refetch();
      qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить чат");
    } finally { setRemoving(false); }
  }
  return <div className="chat-panel flex h-full min-h-0 min-w-0 flex-col">
    <div className="flex items-center gap-2 border-b border-ink-700/50 px-4 py-3">
      <Bot className="h-5 w-5 shrink-0 text-accent-300" />
      <select aria-label="История чатов" value={chatId || ""} disabled={sending || !ready} onChange={e => select(e.target.value || null)} className="min-w-0 flex-1 rounded-lg bg-ink-900 p-2 text-sm">
        <option value="">Новый чат</option>{history.data?.map(c => <option key={c.id} value={c.id}>{c.title || "Чат"}</option>)}
      </select>
      {detail.data?.project_id && <button onClick={zip} className="icon-button" aria-label="Скачать файлы чата ZIP"><Download className="h-4 w-4" /></button>}
      {chatId && <button aria-label="Удалить чат" title={running ? "Сначала остановите задачу" : "Удалить чат"} onClick={removeChat} disabled={sending || removing || running || !ready} className="icon-button hover:bg-red-500/15 hover:text-red-300">{removing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}</button>}
      <button aria-label="Новый чат" onClick={() => select(null)} disabled={sending || !ready} className="icon-button"><Plus className="h-5 w-5" /></button>
    </div>
    <div ref={scroll} className="chat-scroll min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
      {history.isError || detail.isError ? <p role="alert" className="text-red-300">Не удалось загрузить историю. <button onClick={() => { history.refetch(); detail.refetch(); }} className="underline">Повторить</button></p> : !ready || (chatId && detail.isPending) ? <p className="text-sm text-neutral-500">Загрузка истории…</p> : !messages.length && <div className="mx-auto my-12 max-w-sm text-center"><div className="empty-orb mx-auto mb-5"><Bot className="h-7 w-7" /></div><h2 className="text-xl font-semibold">Что сделаем сегодня?</h2><p className="mt-3 text-sm leading-relaxed text-neutral-400">Опишите задачу. Можно переключаться между разделами и создавать новые чаты — работа продолжится, история и файлы сохранятся.</p></div>}
      {messages.map(m => <article key={m.id} className={`min-w-0 ${m.role === "user" ? "ml-auto max-w-[90%] rounded-2xl bg-accent-500/15 p-4" : "rounded-2xl bg-ink-800/40 p-4"}`}>
        <p className="mb-2 text-xs font-semibold text-neutral-500">{m.role === "user" ? "Вы" : "Layla"}</p>
        {m.meta?.reasoning && <details className="mb-3 rounded-lg border border-ink-700 p-3"><summary className="cursor-pointer text-xs text-neutral-400">Размышление</summary><p className="mt-3 whitespace-pre-wrap break-words text-xs leading-relaxed text-neutral-400">{m.meta.reasoning}</p></details>}
        <div className="whitespace-pre-wrap break-words text-sm leading-7">{m.content}</div>
        {m.meta?.tools?.map(tool => <div key={tool.id} className="mt-3">{tool.change ? <FileDiff change={tool.change} onOpen={onOpenFile} /> : <div className={`rounded-lg border p-3 text-xs ${tool.status === "error" ? "border-red-500/30 text-red-300" : "border-ink-700 text-neutral-400"}`}><span>{tool.status === "running" ? "Выполняется" : tool.status === "done" ? "Готово" : "Ошибка"} · {tool.name}</span><p className="mt-1 break-all font-mono">{tool.path}</p>{tool.error && <p>{tool.error}</p>}</div>}</div>)}
        {m.meta?.error && <p role="alert" className="mt-3 text-sm text-red-300">{m.meta.error}</p>}
      </article>)}
    </div>
    {error && <p role="alert" className="mx-5 mb-3 text-sm text-red-300">{error}</p>}
    {job && <div role="status" className="mx-5 mb-3 flex items-center gap-2 text-xs text-neutral-400">{running && <Loader2 className="h-4 w-4 animate-spin" />}<span className="min-w-0 flex-1 break-words">{running ? job.steps.at(-1)?.text || "Задача в очереди" : job.status === "error" ? job.error || "Ошибка выполнения" : job.status === "cancelled" ? "Остановлено" : "Готово"}</span>{running && <button onClick={stop} className="icon-button" aria-label="Остановить задачу"><Square className="h-3 w-3" /></button>}</div>}
    <div className="mx-4 mb-4 rounded-2xl border border-ink-600/60 bg-ink-800/70 p-3">
      <div className="mb-3 flex flex-wrap gap-2 text-xs">
        <select aria-label="Персона" value={personaId} disabled={!!chatId || sending} onChange={e => setPersonaId(e.target.value)} className="min-w-0 max-w-full rounded-lg bg-ink-900 px-3 py-2"><option value="">Без персоны</option>{personas.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        <select aria-label="Модель" value={choice} disabled={running || sending} onChange={e => pickModel(e.target.value)} className="min-w-0 max-w-full rounded-lg bg-ink-900 px-3 py-2">{!models.length && <option value="">Нет активных моделей</option>}{models.map(m => <option key={`${m.provider_id}|${m.name}`} value={`${m.provider_id}|${m.name}`}>{m.name} · {m.provider}</option>)}</select>
      </div>
      <div className="flex items-end gap-3"><textarea aria-label="Сообщение агенту" rows={3} value={input} onChange={e => { setInput(e.target.value); remember(`${scope}:draft:${chatId || "new"}`, e.target.value); }} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} placeholder={running ? "Задача выполняется. Для другой задачи создайте новый чат." : "Опишите задачу…"} className="min-w-0 flex-1 resize-none bg-transparent p-2 text-sm outline-none" /><button aria-label="Отправить" onClick={send} disabled={!input.trim() || running || sending || !ready} className="primary-button h-11 w-11 shrink-0 !p-0">{sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}</button></div>
      <p className="px-2 pt-2 text-[11px] text-neutral-500">Работа продолжается в фоне. Файлы и диалог сохраняются автоматически.</p>
    </div>
  </div>;
}
