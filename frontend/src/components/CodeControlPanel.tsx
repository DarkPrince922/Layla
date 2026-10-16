"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Run = { text: string; mode: "auto" | "confirm" | "plan" | "review"; planId?: string; resumeId?: string };
type Plan = { id: string; title: string; content: string; status: string; feedback: string };
type Usage = { max_tokens: number | null; max_usd: string | null; tokens_used: number; estimated_usd: string;
  prices: { input?: string; output?: string; web_search?: string }; calls: Record<string, number>; model: string | null; note: string };
type Control = { plans: Plan[]; recovery: { message_id: string; interrupted: boolean; error?: string; goal: string } | null; usage: Usage };

export function CodeControlPanel({ chatId, running, onRun }: { chatId: string; running: boolean; onRun: (args: Run) => Promise<void> }) {
  const qc = useQueryClient();
  const endpoint = `/chats/${chatId}/code-control`;
  const state = useQuery({ queryKey: ["code-control", chatId, running], queryFn: () => api.get<Control>(endpoint),
    refetchInterval: running ? 1500 : false });
  const [feedback, setFeedback] = useState("");
  const [tokens, setTokens] = useState("");
  const [usd, setUsd] = useState("");
  const [inputPrice, setInputPrice] = useState("");
  const [outputPrice, setOutputPrice] = useState("");
  const [searchPrice, setSearchPrice] = useState("10");
  const [dirty, setDirty] = useState(false);
  const usage = state.data?.usage;
  useEffect(() => {
    if (!usage || dirty) return;
    setTokens(usage.max_tokens?.toString() || ""); setUsd(usage.max_usd || "");
    setInputPrice(usage.prices.input || ""); setOutputPrice(usage.prices.output || "");
    setSearchPrice(usage.prices.web_search || "10");
  }, [usage, dirty]);
  const reject = useMutation({ mutationFn: (id: string) => api.post(`${endpoint}/plans/${id}/reject`, { feedback }),
    onSuccess: () => { setFeedback(""); qc.invalidateQueries({ queryKey: ["code-control", chatId] }); } });
  const save = useMutation({ mutationFn: () => api.patch(`${endpoint}/usage`, {
    max_tokens: tokens ? Number(tokens) : null, max_usd: usd || null,
    input_usd_per_million: inputPrice || null, output_usd_per_million: outputPrice || null,
    web_search_usd_per_thousand: searchPrice || "10",
  }), onSuccess: () => { setDirty(false); qc.invalidateQueries({ queryKey: ["code-control", chatId] }); } });
  const [starting, setStarting] = useState(false);
  async function run(args: Run) {
    setStarting(true);
    try { await onRun(args); }
    finally { setStarting(false); qc.invalidateQueries({ queryKey: ["code-control", chatId] }); }
  }
  const plan = state.data?.plans.find(row => row.status === "pending");
  const recovery = state.data?.recovery;
  return <div className="space-y-3 text-xs">
    {plan && <section className="space-y-3 rounded-xl border border-accent-500/30 bg-accent-500/5 p-3" aria-label="Утверждение плана">
      <p className="font-medium text-accent-100">План ждёт вашего решения · {plan.title}</p>
      <details><summary className="cursor-pointer text-neutral-400">Посмотреть утверждаемый текст</summary>
        <p className="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-words text-neutral-300">{plan.content}</p></details>
      <div className="flex flex-wrap gap-2">
        <button disabled={running || starting} className="primary-button !py-1.5 text-xs" onClick={() => run({ text: "Выполни утверждённый план.", mode: "auto", planId: plan.id })}>Утвердить и выполнить</button>
        <button disabled={running || starting} className="secondary-button !py-1.5 text-xs" onClick={() => run({ text: "Выполни утверждённый план с подтверждением действий.", mode: "confirm", planId: plan.id })}>Утвердить · подтверждать действия</button>
      </div>
      <input aria-label="Замечания к плану" value={feedback} onChange={e => setFeedback(e.target.value)} placeholder="Что изменить в плане" maxLength={8000} className="w-full rounded bg-ink-900 p-2" />
      <button disabled={running || reject.isPending} onClick={() => reject.mutate(plan.id)} className="text-neutral-400">Отклонить и сохранить замечания</button>
    </section>}
    {recovery?.interrupted && !running && <section className="rounded-xl border border-amber-500/30 p-3">
      <p className="text-amber-200">Ход прерван · {recovery.error}</p>
      <p className="mt-1 text-neutral-400">Выполненные действия сохранены. Агент проверит неизвестные результаты перед повтором.</p>
      <button disabled={starting} onClick={() => run({ text: "Продолжи прерванную задачу, учитывая сохранённый прогресс.", mode: "confirm", resumeId: recovery.message_id })} className="mt-2 text-accent-300">Продолжить с подтверждением действий</button>
    </section>}
    <details className="rounded-xl border border-ink-700 p-3">
      <summary className="cursor-pointer text-neutral-300">Использование и бюджет · {usage?.tokens_used.toLocaleString() || 0} токенов с резервом · оценка ${usage?.estimated_usd || "0"}</summary>
      <div className="mt-3 space-y-3">
        <p className="text-neutral-500">Общий бюджет чата для ведущего, воркеров, сжатия и повторных запросов. Пустой лимит означает отсутствие ограничения. Смена модели не обнуляет расход.</p>
        {!!usage?.calls.reserved && <p className="text-neutral-400">Запросов с несверенным резервом: {usage.calls.reserved}.</p>}
        {!!usage?.calls.estimated && <p className="text-neutral-400">Без отчёта провайдера: {usage.calls.estimated} запросов; учтён полный резерв.</p>}
        {!!usage?.calls.uncertain && <p className="text-amber-200">Неизвестный расход: {usage.calls.uncertain} запросов. Резерв сохранён.</p>}
        <form className="space-y-2" onSubmit={e => { e.preventDefault(); save.mutate(); }}>
          <label className="block">Лимит токенов<input type="number" min={1} step={1} value={tokens} onChange={e => { setDirty(true); setTokens(e.target.value); }} className="ml-2 rounded bg-ink-900 p-2" /></label>
          <label className="block">Лимит USD<input type="number" min={0.000001} step="any" value={usd} onChange={e => { setDirty(true); setUsd(e.target.value); }} className="ml-2 rounded bg-ink-900 p-2" /></label>
          <p className="text-neutral-500">Цены для модели {usage?.model || "чата"}, USD за 1 млн токенов. Их нужно задать вручную; это оценка по указанным ценам, а не счёт провайдера.</p>
          <label className="block">Вход<input type="number" min={0} step="any" value={inputPrice} onChange={e => { setDirty(true); setInputPrice(e.target.value); }} className="ml-2 rounded bg-ink-900 p-2" /></label>
          <label className="block">Выход<input type="number" min={0} step="any" value={outputPrice} onChange={e => { setDirty(true); setOutputPrice(e.target.value); }} className="ml-2 rounded bg-ink-900 p-2" /></label>
          <label className="block">WebSearch, USD за 1000 поисков<input type="number" min={0} step="any" value={searchPrice} onChange={e => { setDirty(true); setSearchPrice(e.target.value); }} className="ml-2 rounded bg-ink-900 p-2" /></label>
          <p className="text-neutral-500">{usage?.note}</p>
          <button disabled={save.isPending} className="secondary-button !py-1.5 text-xs">Сохранить бюджет</button>
        </form>
      </div>
    </details>
    {(state.error || save.error || reject.error) && <p role="alert" className="text-red-300">{state.error?.message || save.error?.message || reject.error?.message}</p>}
  </div>;
}
