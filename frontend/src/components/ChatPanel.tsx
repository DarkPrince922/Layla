"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, Bot, User as UserIcon, Plus, Loader2 } from "lucide-react";
import {
  api,
  type Persona,
  type ModelInfo,
  type Chat,
  type ChatDetail,
  type ChatMessage,
  type FileChange,
  type Job,
} from "@/lib/api";
import { FileDiff } from "@/components/FileDiff";

const toolLabels: Record<string, string> = {
  list_files: "Обзор папки",
  read_file: "Чтение",
  write_file: "Запись",
  delete_file: "Удаление",
};
const hydrate = (m: ChatMessage): ChatMessage => ({
  ...m,
  reasoning: m.meta?.reasoning,
  tools: m.meta?.tools,
  error: m.meta?.error,
});

// Все чаты выполняются в ФОНЕ: отправка создаёт фоновую задачу, ответ пишется в
// историю на сервере. Панель опрашивает чат, пока задача активна, и заново
// привязывается к ней при возврате в домен — диалог не теряется.
export function ChatPanel({
  domain,
  projectId,
  onFileChange,
}: {
  domain: string;
  projectId?: string;
  onFileChange?: (change: FileChange) => void;
}) {
  const qc = useQueryClient();
  const [chatId, setChatId] = useState<string | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [personaId, setPersonaId] = useState("");
  const [model, setModel] = useState("");
  const [running, setRunning] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const seqRef = useRef(0);
  const appliedTools = useRef<Set<string>>(new Set());

  const lastKey = `layla:lastChat:${domain}:${projectId || ""}`;

  const { data: personas = [] } = useQuery({
    queryKey: ["personas"],
    queryFn: () => api.get<Persona[]>("/personas"),
  });
  const { data: models = [] } = useQuery({
    queryKey: ["models"],
    queryFn: () => api.get<ModelInfo[]>("/models"),
  });
  const canWrite =
    !personaId ||
    personas.find((p) => p.id === personaId)?.allowed_tools?.includes("files.write");

  useEffect(() => {
    if (!model && models.length) setModel(models[0].name);
  }, [models, model]);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  function stopPoll() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setRunning(false);
  }

  function applyChanges(msgs: ChatMessage[]) {
    if (!onFileChange) return;
    for (const m of msgs) {
      for (const t of m.tools || []) {
        if (t.change && !appliedTools.current.has(t.id)) {
          appliedTools.current.add(t.id);
          onFileChange(t.change);
        }
      }
    }
  }

  function startPoll(id: string) {
    stopPoll();
    setRunning(true);
    pollRef.current = setInterval(async () => {
      try {
        const detail = await api.get<ChatDetail>(`/chats/${id}`);
        const hydrated = detail.messages.map(hydrate);
        setMessages(hydrated);
        applyChanges(hydrated);
        const active = await api.get<Job[]>("/jobs?active=true");
        const job = active.find(
          (j) => (j.result as { chat_id?: string })?.chat_id === id,
        );
        if (!job) {
          stopPoll();
          qc.invalidateQueries({ queryKey: ["jobs"] });
        }
      } catch {
        /* временную ошибку опроса игнорируем */
      }
    }, 1300);
  }

  async function openChat(id: string, remember = true) {
    const seq = ++seqRef.current;
    setChatId(id);
    setError(null);
    appliedTools.current = new Set();
    if (remember) {
      try {
        localStorage.setItem(lastKey, id);
      } catch {
        /* ignore */
      }
    }
    try {
      const detail = await api.get<ChatDetail>(`/chats/${id}`);
      if (seq !== seqRef.current) return;
      const hydrated = detail.messages.map(hydrate);
      setMessages(hydrated);
      // Пометить уже применённые изменения, чтобы не дублировать в редакторе.
      for (const m of hydrated) for (const t of m.tools || []) if (t.change) appliedTools.current.add(t.id);
      setPersonaId(detail.persona_id || "");
      if (detail.model) setModel(detail.model);
      const active = await api.get<Job[]>("/jobs?active=true");
      const job = active.find((j) => (j.result as { chat_id?: string })?.chat_id === id);
      if (job) startPoll(id);
      else stopPoll();
    } catch (e) {
      if (seq === seqRef.current)
        setError(e instanceof Error ? e.message : "Не удалось загрузить историю");
    }
  }

  // Загрузка чатов домена/проекта и восстановление последнего активного.
  useEffect(() => {
    let alive = true;
    stopPoll();
    (async () => {
      setLoadingHistory(true);
      try {
        const q = projectId ? `?project_id=${projectId}` : `?domain=${domain}`;
        const list = await api.get<Chat[]>(`/chats${q}`);
        if (!alive) return;
        setChats(list);
        let stored = "";
        try {
          stored = localStorage.getItem(lastKey) || "";
        } catch {
          /* ignore */
        }
        const pick = list.find((c) => c.id === stored) || list[0] || null;
        if (pick) await openChat(pick.id, false);
        else {
          setChatId(null);
          setMessages([]);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "Не удалось загрузить чаты");
      } finally {
        if (alive) setLoadingHistory(false);
      }
    })();
    return () => {
      alive = false;
      stopPoll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain, projectId]);

  useEffect(() => () => stopPoll(), []);

  async function ensureChat(content: string): Promise<string> {
    if (chatId) return chatId;
    const chat = await api.post<Chat>("/chats", {
      domain,
      project_id: projectId || null,
      title: content.slice(0, 80),
      persona_id: personaId || null,
      model: model || null,
    });
    setChatId(chat.id);
    setChats((old) => [chat, ...old]);
    try {
      localStorage.setItem(lastKey, chat.id);
    } catch {
      /* ignore */
    }
    return chat.id;
  }

  async function newChat() {
    if (running) return;
    seqRef.current++;
    stopPoll();
    setChatId(null);
    setMessages([]);
    setPersonaId("");
    setError(null);
    appliedTools.current = new Set();
    try {
      localStorage.removeItem(lastKey);
    } catch {
      /* ignore */
    }
  }

  async function send() {
    const content = input.trim();
    if (!content || running || loadingHistory) return;
    if (!model) {
      setError("Добавьте и активируйте провайдера в настройках, чтобы выбрать модель.");
      return;
    }
    setError(null);
    setInput("");
    setMessages((m) => [
      ...m,
      { id: `u-${Date.now()}`, role: "user", content },
      { id: `a-${Date.now()}`, role: "assistant", content: "", tools: [] },
    ]);
    try {
      const id = await ensureChat(content);
      await api.post(`/chats/${id}/run`, { content, model });
      qc.invalidateQueries({ queryKey: ["jobs", "active"] });
      startPoll(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось запустить задачу");
      setMessages((m) => m.filter((x) => !x.id.startsWith("a-") || x.content));
    }
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-2">
        <Bot className="h-4 w-4 shrink-0 text-indigo-400" />
        <select
          aria-label="История чатов"
          value={chatId || ""}
          disabled={running || loadingHistory}
          onChange={(e) => (e.target.value ? openChat(e.target.value) : newChat())}
          className="min-w-0 flex-1 rounded bg-ink-900 p-1 text-xs"
        >
          <option value="">Новый чат</option>
          {chats.map((chat) => (
            <option key={chat.id} value={chat.id}>
              {chat.title || "Чат"}
            </option>
          ))}
        </select>
        <button
          aria-label="Новый чат"
          onClick={newChat}
          disabled={running || loadingHistory}
          className="rounded p-1 text-neutral-400 hover:bg-ink-700 disabled:opacity-40"
        >
          <Plus className="h-4 w-4" />
        </button>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
        {loadingHistory ? (
          <p className="text-center text-xs text-neutral-500">Загрузка истории…</p>
        ) : (
          messages.length === 0 && (
            <div className="mx-auto mt-10 max-w-xs text-center">
              <Bot className="mx-auto mb-3 h-8 w-8 text-indigo-400/70" />
              <p className="text-sm text-neutral-300">
                {projectId ? "Что создадим?" : "Начните диалог"}
              </p>
              <p className="mt-2 text-xs leading-relaxed text-neutral-500">
                Задача выполняется в фоне — можно закрыть панель или сменить домен,
                диалог сохранится.
              </p>
            </div>
          )
        )}
        {messages.map((m) => (
          <div key={m.id} className="flex gap-2.5">
            <div className="mt-0.5 shrink-0">
              {m.role === "assistant" ? (
                <Bot className="h-4 w-4 text-indigo-400" />
              ) : (
                <UserIcon className="h-4 w-4 text-neutral-500" />
              )}
            </div>
            <div className="min-w-0 flex-1">
              {m.reasoning && (
                <details className="mb-2 rounded-md border border-ink-700 bg-ink-800/60">
                  <summary className="cursor-pointer px-2 py-1 text-[11px] text-neutral-400">
                    Размышление
                  </summary>
                  <div className="whitespace-pre-wrap break-words border-t border-ink-700 px-2 py-1.5 text-xs text-neutral-500">
                    {m.reasoning}
                  </div>
                </details>
              )}
              <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-neutral-200">
                {m.content ||
                  (!m.tools?.length && !m.error && (
                    <span className="text-neutral-600">…</span>
                  ))}
              </div>
              {m.tools?.map((tool) => (
                <div key={tool.id} className="mt-2">
                  {tool.change ? (
                    <FileDiff change={tool.change} />
                  ) : (
                    <div
                      className={`rounded-lg border px-2.5 py-2 text-[11px] ${tool.status === "error" ? "border-red-500/20 text-red-300" : "border-ink-700 text-neutral-400"}`}
                    >
                      <span className={tool.status === "running" ? "animate-pulse" : ""}>
                        {tool.status === "running" ? "◌" : tool.status === "done" ? "✓" : "!"}{" "}
                        {toolLabels[tool.name] || tool.name}
                      </span>{" "}
                      <span className="break-all font-mono">{tool.path}</span>
                      {tool.error && <p className="mt-1">{tool.error}</p>}
                    </div>
                  )}
                </div>
              ))}
              {m.error && (
                <p
                  role="alert"
                  className="mt-2 rounded border border-red-500/20 bg-red-500/10 p-2 text-xs text-red-300"
                >
                  {m.error}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>

      {error && (
        <p
          role="alert"
          className="mx-3 mb-2 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300"
        >
          {error}
        </p>
      )}

      <div className="shrink-0 border-t border-ink-700 bg-ink-900 p-3">
        <div className="mb-2 flex min-w-0 flex-wrap gap-2 text-xs">
          <select
            aria-label="Персона"
            value={personaId}
            disabled={running || !!chatId}
            onChange={(e) => setPersonaId(e.target.value)}
            className="min-w-0 max-w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 disabled:opacity-50"
          >
            <option value="">Без персоны</option>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <select
            aria-label="Модель"
            value={model}
            disabled={running}
            onChange={(e) => setModel(e.target.value)}
            className="min-w-0 max-w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5"
          >
            {models.length === 0 && <option value="">Нет активных моделей</option>}
            {models.map((m) => (
              <option key={`${m.provider_id}:${m.name}`} value={m.name}>
                {m.name} · {m.provider}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-end gap-2">
          <textarea
            aria-label="Сообщение агенту"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            rows={3}
            placeholder={running ? "Задача выполняется в фоне…" : projectId ? "Опишите задачу…" : "Спросите что-нибудь…"}
            className="min-w-0 flex-1 resize-none rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-indigo-500"
          />
          <button
            onClick={send}
            disabled={!input.trim() || running || loadingHistory}
            aria-label="Отправить"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </button>
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-neutral-500">
          {running
            ? "Задача идёт в фоне — прогресс в панели «В работе». Можно закрыть панель или сменить домен."
            : projectId
              ? canWrite
                ? "Агент работает в фоне: создаёт/меняет файлы проекта; всё сохраняется в истории."
                : "Файловые действия ограничены правами выбранной персоны."
              : "Ответ готовится в фоне и сохраняется — диалог не пропадёт при переключении."}
        </p>
      </div>
    </div>
  );
}
