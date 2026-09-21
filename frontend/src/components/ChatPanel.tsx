"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, Bot, User as UserIcon, RefreshCw } from "lucide-react";
import { api, streamChat, type Persona, type ModelInfo, type ChatMessage } from "@/lib/api";

export function ChatPanel({ domain }: { domain: string }) {
  const [chatId, setChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [personaId, setPersonaId] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();

  const { data: personas = [] } = useQuery({
    queryKey: ["personas"],
    queryFn: () => api.get<Persona[]>("/personas"),
  });
  const { data: models = [] } = useQuery({
    queryKey: ["models"],
    queryFn: () => api.get<ModelInfo[]>("/models"),
  });

  // Подтянуть реальный список моделей у провайдеров (GET /models на их стороне).
  async function refreshModels() {
    setRefreshing(true);
    setError(null);
    try {
      const fresh = await api.get<ModelInfo[]>("/models?refresh=true");
      qc.setQueryData(["models"], fresh);
      if (fresh.length && !fresh.some((m) => m.name === model)) setModel(fresh[0].name);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить модели");
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (!model && models.length) setModel(models[0].name);
  }, [models, model]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  async function ensureChat(): Promise<string> {
    if (chatId) return chatId;
    const chat = await api.post<{ id: string }>("/chats", {
      domain,
      persona_id: personaId || null,
      model: model || null,
    });
    setChatId(chat.id);
    return chat.id;
  }

  async function send() {
    const content = input.trim();
    if (!content || busy) return;
    if (!model) {
      setError("Сначала добавьте и активируйте провайдера в настройках, чтобы выбрать модель.");
      return;
    }
    setError(null);
    setBusy(true);
    setInput("");
    setMessages((m) => [...m, { id: `u-${Date.now()}`, role: "user", content }]);
    const assistantId = `a-${Date.now()}`;
    setMessages((m) => [...m, { id: assistantId, role: "assistant", content: "" }]);

    try {
      const id = await ensureChat();
      await streamChat(id, content, model, {
        onDelta: (delta) =>
          setMessages((m) =>
            m.map((msg) => (msg.id === assistantId ? { ...msg, content: msg.content + delta } : msg)),
          ),
        onReasoning: (r) =>
          setMessages((m) =>
            m.map((msg) =>
              msg.id === assistantId ? { ...msg, reasoning: (msg.reasoning || "") + r } : msg,
            ),
          ),
        onError: (msg) => setError(msg),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка отправки");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="mt-8 text-center text-sm text-neutral-600">
            Начните диалог. Выберите персону и модель ниже.
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className="flex gap-3">
            <div className="mt-0.5 shrink-0">
              {m.role === "assistant" ? (
                <Bot className="h-5 w-5 text-indigo-400" />
              ) : (
                <UserIcon className="h-5 w-5 text-neutral-500" />
              )}
            </div>
            <div className="min-w-0 flex-1">
              {m.reasoning && (
                <details className="mb-2 rounded-md border border-ink-700 bg-ink-800/60" open={!m.content}>
                  <summary className="cursor-pointer select-none px-2 py-1 text-[11px] text-neutral-400">
                    💭 Размышление
                  </summary>
                  <div className="whitespace-pre-wrap border-t border-ink-700 px-2 py-1.5 text-xs italic leading-relaxed text-neutral-500">
                    {m.reasoning}
                  </div>
                </details>
              )}
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-neutral-200">
                {m.content || (!m.reasoning && <span className="text-neutral-600">…</span>)}
              </div>
            </div>
          </div>
        ))}
      </div>

      {error && (
        <div className="mx-4 mb-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}

      {/* Composer */}
      <div className="border-t border-ink-700 bg-ink-900 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          <select
            value={personaId}
            onChange={(e) => setPersonaId(e.target.value)}
            className="rounded-md border border-ink-700 bg-ink-800 px-2 py-1"
          >
            <option value="">Без персоны</option>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="rounded-md border border-ink-700 bg-ink-800 px-2 py-1"
          >
            {models.length === 0 && <option value="">Нет активных моделей</option>}
            {models.map((m) => (
              <option key={`${m.provider_id}:${m.name}`} value={m.name}>
                {m.name} · {m.provider}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={refreshModels}
            disabled={refreshing}
            title="Загрузить модели у провайдера"
            className="flex items-center gap-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1 text-neutral-400 hover:text-neutral-200 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            модели
          </button>
        </div>
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={2}
            placeholder="Спросите что-нибудь… (Enter — отправить, Shift+Enter — новая строка)"
            className="flex-1 resize-none rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-indigo-500"
          />
          <button
            onClick={send}
            disabled={busy || !input.trim()}
            className="grid h-9 w-9 place-items-center rounded-md bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40"
            aria-label="Отправить"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
