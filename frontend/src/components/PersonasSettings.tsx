"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Persona {
  id: string;
  name: string;
  kind: string;
  color?: string | null;
  instructions?: string | null;
  is_builtin: boolean;
  hitl_required: boolean;
}

export function PersonasSettings() {
  const { data: personas = [], isLoading } = useQuery({
    queryKey: ["personas"],
    queryFn: () => api.get<Persona[]>("/personas"),
  });

  return (
    <div>
      <h1 className="text-xl font-semibold">Персоны</h1>
      <p className="mb-4 text-sm text-neutral-500">
        Персона переключает роль ИИ, доступ к инструментам и рамки на уровне
        чата. Встроенные персоны уже готовы; пользовательские появятся на
        следующем этапе.
      </p>
      {isLoading ? (
        <p className="text-sm text-neutral-500">Загрузка…</p>
      ) : (
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {personas.map((p) => (
            <li key={p.id} className="rounded-xl border border-ink-700/70 bg-ink-800/30 p-5">
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: p.color || "#666" }}
                />
                <span className="text-sm font-medium">{p.name}</span>
                {p.is_builtin && (
                  <span className="rounded bg-ink-700 px-1.5 py-0.5 text-[11px] text-neutral-400">
                    встроенная
                  </span>
                )}
                {p.hitl_required && (
                  <span className="ml-auto rounded bg-amber-500/20 px-1.5 py-0.5 text-[11px] text-amber-300">
                    HITL
                  </span>
                )}
              </div>
              <p className="mt-2 line-clamp-3 text-xs text-neutral-500">{p.instructions}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
