"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, Bot, User as UserIcon, Square, Plus, Moon } from "lucide-react";
import {
  api,
  streamChat,
  type Persona,
  type ModelInfo,
  type Chat,
  type ChatDetail,
  type ChatMessage,
  type FileChange,
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

export function ChatPanel({
  domain,
  projectId,
  onFileChange,
}: {
  domain: string;
  projectId?: string;
  onFileChange?: (change: FileChange) => void;
}) {
  const [chatId, setChatId] = useState<string | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [personaId, setPersonaId] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(!!projectId);
  const [error, setError] = useState<string | null>(null);
  const [bgNote, setBgNote] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const historySequence = useRef(0);
  const qc = useQueryClient();

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
    personas
      .find((p) => p.id === personaId)
      ?.allowed_tools?.includes("files.write");
  useEffect(() => {
    if (!model && models.length) setModel(models[0].name);
  }, [models, model]);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);
  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    (async () => {
      try {
        const list = await api.get<Chat[]>(`/chats?project_id=${projectId}`);
        if (!alive) return;
        setChats(list);
        if (list[0]) {
          const detail = await api.get<ChatDetail>(`/chats/${list[0].id}`);
          if (!alive) return;
          setChatId(detail.id);
          setMessages(detail.messages.map(hydrate));
          setPersonaId(detail.persona_id || "");
          if (detail.model) setModel(detail.model);
        }
      } catch (e) {
        if (alive)
          setError(
            e instanceof Error ? e.message : "Не удалось загрузить историю",
          );
      } finally {
        if (alive) setLoadingHistory(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [projectId]);

  async function selectChat(id: string) {
    if (busy) return;
    const seq = ++historySequence.current;
    setChatId(id || null);
    setMessages([]);
    setError(null);
    if (!id) {
      setPersonaId("");
      return;
    }
    setLoadingHistory(true);
    try {
      const detail = await api.get<ChatDetail>(`/chats/${id}`);
      if (seq !== historySequence.current) return;
      setMessages(detail.messages.map(hydrate));
      setPersonaId(detail.persona_id || "");
      if (detail.model) setModel(detail.model);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить историю");
    } finally {
      if (seq === historySequence.current) setLoadingHistory(false);
    }
  }

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
    return chat.id;
  }

  async function send() {
    const content = input.trim();
    if (!content || busy || loadingHistory) return;
    if (!model) {
      setError(
        "Добавьте и активируйте провайдера в настройках, чтобы выбрать модель.",
      );
      return;
    }
    setError(null);
    setBusy(true);
    setInput("");
    const assistantId = `a-${Date.now()}`;
    setMessages((m) => [
      ...m,
      { id: `u-${Date.now()}`, role: "user", content },
      { id: assistantId, role: "assistant", content: "", tools: [] },
    ]);
    const controller = new AbortController();
    abortRef.current = controller;
    const update = (fn: (message: ChatMessage) => ChatMessage) =>
      setMessages((m) =>
        m.map((msg) => (msg.id === assistantId ? fn(msg) : msg)),
      );
    try {
      const id = await ensureChat(content);
      if (controller.signal.aborted) return;
      await streamChat(
        id,
        content,
        model,
        {
          onDelta: (delta) =>
            update((msg) => ({ ...msg, content: msg.content + delta })),
          onReasoning: (reasoning) =>
            update((msg) => ({
              ...msg,
              reasoning: (msg.reasoning || "") + reasoning,
            })),
          onTool: (tool) => {
            update((msg) => ({
              ...msg,
              tools: (msg.tools || []).some((t) => t.id === tool.id)
                ? msg.tools!.map((t) => (t.id === tool.id ? tool : t))
                : [...(msg.tools || []), tool],
            }));
            if (tool.change) onFileChange?.(tool.change);
          },
          onError: (message) => update((msg) => ({ ...msg, error: message })),
          onDone: (messageId) => update((msg) => ({ ...msg, id: messageId })),
        },
        controller.signal,
      );
    } catch (e) {
      if (!controller.signal.aborted) {
        update((msg) => ({
          ...msg,
          error: e instanceof Error ? e.message : "Ошибка отправки",
        }));
        setInput(content);
      }
    } finally {
      update((msg) => ({
        ...msg,
        tools: msg.tools?.map((t) =>
          t.status === "running"
            ? {
                ...t,
                status: "error",
                error: "Вызов прерван. Проверьте файл перед продолжением.",
              }
            : t,
        ),
      }));
      setBusy(false);
      abortRef.current = null;
    }
  }

  async function runBackground() {
    const content = input.trim();
    if (!content || busy || loadingHistory) return;
    if (!model) {
      setError("Добавьте и активируйте провайдера в настройках, чтобы выбрать модель.");
      return;
    }
    setError(null);
    setBgNote(null);
    try {
      const id = await ensureChat(content);
      await api.post(`/chats/${id}/agent-run`, { content, model });
      setInput("");
      qc.invalidateQueries({ queryKey: ["jobs", "active"] });
      setBgNote(
        "Задача запущена в фоне — следите за прогрессом в панели «В работе» слева. Можно переключиться в другой домен; результат появится в этом чате по завершении.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось запустить фоновую задачу");
    }
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      {projectId && (
        <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-2">
          <Bot className="h-4 w-4 shrink-0 text-indigo-400" />
          <select
            aria-label="История чатов проекта"
            value={chatId || ""}
            disabled={busy || loadingHistory}
            onChange={(e) => selectChat(e.target.value)}
            className="min-w-0 flex-1 rounded bg-ink-900 p-1 text-xs"
          >
            <option value="">Новый чат проекта</option>
            {chats.map((chat) => (
              <option key={chat.id} value={chat.id}>
                {chat.title || "Чат проекта"}
              </option>
            ))}
          </select>
          <button
            aria-label="Новый чат"
            onClick={() => selectChat("")}
            disabled={busy || loadingHistory}
            className="rounded p-1 text-neutral-400 hover:bg-ink-700 disabled:opacity-40"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
      )}
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4"
      >
        {loadingHistory ? (
          <p className="text-center text-xs text-neutral-500">
            Загрузка истории…
          </p>
        ) : (
          messages.length === 0 && (
            <div className="mx-auto mt-10 max-w-xs text-center">
              <Bot className="mx-auto mb-3 h-8 w-8 text-indigo-400/70" />
              <p className="text-sm text-neutral-300">
                {projectId ? "Что создадим?" : "Начните диалог"}
              </p>
              <p className="mt-2 text-xs leading-relaxed text-neutral-500">
                {projectId
                  ? "Опишите задачу. Агент создаст и изменит файлы этого проекта, а здесь появятся дифы."
                  : "Выберите персону и модель ниже."}
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
                      <span
                        className={
                          tool.status === "running" ? "animate-pulse" : ""
                        }
                      >
                        {tool.status === "running"
                          ? "◌"
                          : tool.status === "done"
                            ? "✓"
                            : "!"}{" "}
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
            disabled={busy || !!chatId}
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
            disabled={busy}
            onChange={(e) => setModel(e.target.value)}
            className="min-w-0 max-w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5"
          >
            {models.length === 0 && (
              <option value="">Нет активных моделей</option>
            )}
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
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                send();
              }
            }}
            rows={3}
            placeholder={projectId ? "Создай проект…" : "Спросите что-нибудь…"}
            className="min-w-0 flex-1 resize-none rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-indigo-500"
          />
          {busy ? (
            <button
              onClick={() => abortRef.current?.abort()}
              aria-label="Остановить"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-ink-600 text-neutral-200"
            >
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <>
              {projectId && (
                <button
                  onClick={runBackground}
                  disabled={!input.trim() || loadingHistory}
                  title="Запустить агента в фоне"
                  aria-label="Запустить в фоне"
                  className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-ink-600 text-neutral-300 hover:bg-ink-700 disabled:opacity-40"
                >
                  <Moon className="h-4 w-4" />
                </button>
              )}
              <button
                onClick={send}
                disabled={!input.trim() || loadingHistory}
                aria-label="Отправить"
                className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            </>
          )}
        </div>
        {bgNote && (
          <p className="mt-2 rounded border border-indigo-500/30 bg-indigo-500/10 px-2 py-1.5 text-[10px] leading-relaxed text-indigo-200">
            {bgNote}
          </p>
        )}
        {projectId && (
          <p className="mt-2 text-[10px] leading-relaxed text-neutral-500">
            {canWrite
              ? "Луна — запустить агента в фоне; изменения применяются к файлам проекта и сохраняются в истории чата."
              : "Файловые действия ограничены правами выбранной персоны."}
          </p>
        )}
      </div>
    </div>
  );
}
