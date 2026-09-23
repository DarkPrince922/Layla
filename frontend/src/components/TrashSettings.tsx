"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Folder, MessageSquare, RotateCcw, Trash2 } from "lucide-react";
import { api, type TrashList } from "@/lib/api";
import { confirmAction } from "@/components/ConfirmDialog";

const DOMAINS: Record<string, string> = { code: "Код", design: "Дизайн", osint: "OSINT", pentest: "Пентест" };

function when(value: string) {
  const date = new Date(/[Z+]/.test(value.slice(10)) ? value : value + "Z");
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function TrashSettings() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({ queryKey: ["trash"], queryFn: () => api.get<TrashList>("/trash") });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(key: string, action: () => Promise<unknown>) {
    setBusy(key); setError(null);
    try {
      await action();
      await Promise.all(["trash", "chats", "projects", "jobs"].map(k => qc.invalidateQueries({ queryKey: [k] })));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не получилось");
    } finally { setBusy(null); }
  }

  const empty = !!data && !data.chats.length && !data.projects.length;
  const row = "flex flex-wrap items-center gap-3 rounded-xl border border-ink-700/70 bg-ink-800/30 px-4 py-3";

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold">Корзина</h1>
          <p className="text-sm text-neutral-500">
            Удалённые чаты и проекты хранятся {data?.days ?? 7} дней — их можно восстановить.
            Потом они стираются окончательно, вместе с файлами.
          </p>
        </div>
        {!empty && data && (
          <button disabled={!!busy} className="secondary-button text-xs hover:text-red-300"
            onClick={async () => {
              if (await confirmAction("Очистить корзину? Всё в ней будет стёрто окончательно, вместе с файлами.", "Очистить"))
                act("all", () => api.del("/trash"));
            }}>
            <Trash2 className="h-4 w-4" />Очистить корзину
          </button>
        )}
      </div>

      {error && <p role="alert" className="mb-3 text-sm text-red-300">{error}</p>}
      {isLoading && <p className="text-sm text-neutral-500">Загрузка…</p>}
      {isError && <p role="alert" className="text-sm text-red-300">Не удалось загрузить корзину.</p>}
      {empty && <p className="rounded-xl border border-dashed border-ink-700 p-8 text-center text-sm text-neutral-500">Корзина пуста.</p>}

      {!!data?.projects.length && (
        <section className="mb-6">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">Проекты</h2>
          <ul className="space-y-2">
            {data.projects.map(p => (
              <li key={p.id} className={row}>
                <Folder className="h-4 w-4 shrink-0 text-accent-300" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{p.name}</p>
                  <p className="text-[11px] text-neutral-500">
                    {p.chats ? `чатов: ${p.chats} · ` : ""}удалён {when(p.deleted_at)} · сотрётся {when(p.purge_at)}
                  </p>
                </div>
                <button disabled={!!busy} className="secondary-button text-xs"
                  onClick={() => act(p.id, () => api.post(`/trash/projects/${p.id}/restore`))}>
                  <RotateCcw className="h-4 w-4" />Восстановить
                </button>
                <button disabled={!!busy} aria-label={`Удалить навсегда ${p.name}`} title="Удалить навсегда" className="icon-button hover:bg-red-500/15 hover:text-red-300"
                  onClick={async () => {
                    if (await confirmAction(`Удалить проект «${p.name}» навсегда? Файлы и чаты проекта будут стёрты.`, "Удалить навсегда"))
                      act(p.id, () => api.del(`/trash/projects/${p.id}`));
                  }}>
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!!data?.chats.length && (
        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">Чаты</h2>
          <ul className="space-y-2">
            {data.chats.map(c => (
              <li key={c.id} className={row}>
                <MessageSquare className="h-4 w-4 shrink-0 text-accent-300" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{c.title || "Без названия"}</p>
                  <p className="text-[11px] text-neutral-500">
                    {DOMAINS[c.domain] || c.domain} · удалён {when(c.deleted_at)} · сотрётся {when(c.purge_at)}
                  </p>
                </div>
                <button disabled={!!busy} className="secondary-button text-xs"
                  onClick={() => act(c.id, () => api.post(`/trash/chats/${c.id}/restore`))}>
                  <RotateCcw className="h-4 w-4" />Восстановить
                </button>
                <button disabled={!!busy} aria-label={`Удалить навсегда ${c.title || "чат"}`} title="Удалить навсегда" className="icon-button hover:bg-red-500/15 hover:text-red-300"
                  onClick={async () => {
                    if (await confirmAction(`Удалить чат «${c.title || "Без названия"}» навсегда? Вместе с его файлами.`, "Удалить навсегда"))
                      act(c.id, () => api.del(`/trash/chats/${c.id}`));
                  }}>
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
