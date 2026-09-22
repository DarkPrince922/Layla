"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Route } from "lucide-react";
import { api } from "@/lib/api";

interface Combo {
  id: string;
  name: string;
  models: string[];
  enabled: boolean;
  cooldown_seconds: number;
}

export function CombosManager() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [models, setModels] = useState("");
  const [routed, setRouted] = useState<Record<string, string>>({});

  const { data: combos = [] } = useQuery({
    queryKey: ["combos"],
    queryFn: () => api.get<Combo[]>("/combos"),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post("/combos", {
        name,
        models: models.split(",").map((m) => m.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["combos"] });
      setName("");
      setModels("");
    },
  });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/combos/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["combos"] }),
  });
  const route = useMutation({
    mutationFn: (id: string) => api.get<{ model: string | null }>(`/combos/${id}/route`),
    onSuccess: (r, id) => setRouted((m) => ({ ...m, [id]: r.model || "нет здоровой модели" })),
  });

  return (
    <div>
      <h2 className="mb-1 text-sm font-semibold text-neutral-300">Combos router</h2>
      <p className="mb-2 text-xs text-neutral-500">
        Наборы моделей с маршрутизацией: выбирается первая здоровая модель, при
        сбое/лимите — lockout и переход к следующей (circuit breaker).
      </p>
      <div className="mb-3 flex flex-wrap items-end gap-2 rounded-lg border border-ink-700 bg-ink-900 p-3">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="название"
          className="rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs" />
        <input value={models} onChange={(e) => setModels(e.target.value)}
          placeholder="модели через запятую (в порядке приоритета)"
          className="min-w-[240px] flex-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs" />
        <button onClick={() => create.mutate()} disabled={!name || !models}
          className="flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-xs text-white disabled:opacity-50">
          <Plus className="h-3.5 w-3.5" /> Создать
        </button>
      </div>

      {combos.length === 0 ? (
        <p className="text-xs text-neutral-600">Комбо ещё нет.</p>
      ) : (
        <ul className="space-y-2">
          {combos.map((c) => (
            <li key={c.id} className="rounded-lg border border-ink-700 bg-ink-900 p-3 text-xs">
              <div className="flex items-center gap-2">
                <span className="font-medium text-neutral-200">{c.name}</span>
                <span className="text-neutral-500">{c.models.join(" → ")}</span>
                <button onClick={() => route.mutate(c.id)}
                  className="ml-auto flex items-center gap-1 rounded bg-ink-700 px-2 py-1 text-[11px] hover:bg-ink-600">
                  <Route className="h-3 w-3" /> route
                </button>
                <button onClick={() => del.mutate(c.id)} className="text-neutral-500 hover:text-red-400">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
              {routed[c.id] && (
                <div className="mt-1 text-[11px] text-emerald-300">→ {routed[c.id]}</div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
