"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AgentConfig } from "@/lib/api";

const PRESETS = [
  { v: "minimal", l: "Minimal" },
  { v: "pulse", l: "Pulse" },
  { v: "constellation", l: "Constellation" },
];
const ROLES = ["explorer", "reviewer", "implementer"];

export function AgentSettings() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["agent-config"],
    queryFn: () => api.get<AgentConfig>("/agent/config"),
  });

  const [preset, setPreset] = useState("minimal");
  const [roleModels, setRoleModels] = useState<Record<string, string>>({});
  const [tokens, setTokens] = useState(0);
  const [cost, setCost] = useState(0);
  const [subMin, setSubMin] = useState(0);
  const [diagCmd, setDiagCmd] = useState("");
  const [diagAuto, setDiagAuto] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!data) return;
    setPreset(data.preset || "minimal");
    setRoleModels(data.role_models || {});
    setTokens(Number(data.budgets?.tokens_per_turn || 0));
    setCost(Number(data.budgets?.cost_usd || 0));
    setSubMin(Number(data.budgets?.subagent_minutes || 0));
    setDiagCmd(String((data.diagnostics as Record<string, unknown>)?.command || ""));
    setDiagAuto(Boolean((data.diagnostics as Record<string, unknown>)?.run_after_edits));
  }, [data]);

  const save = useMutation({
    mutationFn: () =>
      api.put("/agent/config", {
        preset,
        role_models: roleModels,
        budgets: { tokens_per_turn: tokens, cost_usd: cost, subagent_minutes: subMin },
        diagnostics: { command: diagCmd, run_after_edits: diagAuto },
        context: {},
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent-config"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    },
  });

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-semibold">Агент (Ultracode)</h1>
      <p className="mb-4 text-sm text-neutral-500">
        Оркестрация суб-агентов, роли, бюджеты и диагностика. Опасные шаги
        приостанавливаются на подтверждение оператора (HITL).
      </p>

      <section className="mb-5">
        <div className="mb-1 text-sm font-semibold">Визуальный стиль графа</div>
        <div className="flex gap-1">
          {PRESETS.map((p) => (
            <button key={p.v} onClick={() => setPreset(p.v)}
              className={`rounded px-3 py-1.5 text-xs ${preset === p.v ? "bg-indigo-600 text-white" : "bg-ink-800 text-neutral-400"}`}>
              {p.l}
            </button>
          ))}
        </div>
      </section>

      <section className="mb-5">
        <div className="mb-1 text-sm font-semibold">Модели ролей</div>
        <div className="space-y-2">
          {ROLES.map((r) => (
            <div key={r} className="flex items-center gap-2">
              <span className="w-28 text-xs capitalize text-neutral-400">{r}</span>
              <input
                value={roleModels[r] || ""}
                onChange={(e) => setRoleModels({ ...roleModels, [r]: e.target.value })}
                placeholder="inherit (наследовать lead-модель)"
                className="flex-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
              />
            </div>
          ))}
        </div>
      </section>

      <section className="mb-5">
        <div className="mb-1 text-sm font-semibold">Бюджеты</div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <label className="text-xs text-neutral-400">
            Токены/ход
            <input type="number" value={tokens} onChange={(e) => setTokens(Number(e.target.value))}
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs" />
          </label>
          <label className="text-xs text-neutral-400">
            Бюджет $, всего
            <input type="number" value={cost} onChange={(e) => setCost(Number(e.target.value))}
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs" />
          </label>
          <label className="text-xs text-neutral-400">
            Лимит суб-агента, мин (0=выкл)
            <input type="number" value={subMin} onChange={(e) => setSubMin(Number(e.target.value))}
              className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs" />
          </label>
        </div>
      </section>

      <section className="mb-5">
        <div className="mb-1 text-sm font-semibold">Диагностика</div>
        <input value={diagCmd} onChange={(e) => setDiagCmd(e.target.value)}
          placeholder="команда type-check/lint, напр. npm run typecheck"
          className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs" />
        <label className="mt-2 flex items-center gap-2 text-xs text-neutral-400">
          <input type="checkbox" checked={diagAuto} onChange={(e) => setDiagAuto(e.target.checked)} />
          Запускать автоматически после правок
        </label>
      </section>

      <button onClick={() => save.mutate()}
        className="rounded-md bg-indigo-600 px-4 py-2 text-sm text-white">
        {save.isPending ? "Сохранение…" : saved ? "Сохранено ✓" : "Сохранить"}
      </button>
    </div>
  );
}
