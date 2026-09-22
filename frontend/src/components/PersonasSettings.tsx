"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Plus, RotateCcw, Save, ShieldCheck, Trash2 } from "lucide-react";
import { api, type ModelInfo, type Persona } from "@/lib/api";
import { confirmAction } from "@/components/ConfirmDialog";

type Files = "none" | "read" | "write";
type Draft = {
  name: string;
  color: string;
  instructions: string;
  files: Files;
  default_mode: "" | "auto" | "confirm" | "plan";
  default_model: string;
};

const MODES = [
  { id: "", label: "Не менять" },
  { id: "auto", label: "Авто" },
  { id: "confirm", label: "С подтверждением" },
  { id: "plan", label: "План" },
] as const;

const filesOf = (tools: string[] = []): Files =>
  tools.includes("files.write") ? "write" : tools.includes("files.read") ? "read" : "none";
const toolsOf = (files: Files) =>
  files === "write" ? ["files.read", "files.write"] : files === "read" ? ["files.read"] : [];
const draftOf = (p: Persona): Draft => ({
  name: p.name,
  color: p.color || "#8b7bd8",
  instructions: p.instructions || "",
  files: filesOf(p.allowed_tools),
  default_mode: (p.default_mode || "") as Draft["default_mode"],
  default_model: p.default_model || "",
});
// Понятные названия прав встроенных ролей (показываются только для чтения).
const RIGHTS: Record<string, string> = {
  "files.read": "чтение файлов",
  "files.write": "запись файлов",
  "shell.local": "локальные команды",
  "repo.git": "git",
  "design.render": "превью дизайна",
  "engagement.read": "данные engagement",
  "findings.write": "находки",
  "venue.exec": "выполнение на площадке",
  "intel.lookup": "запросы intelligence",
  "osint.case": "кейсы OSINT",
};

export function PersonasSettings() {
  const qc = useQueryClient();
  const { data: personas = [], isLoading } = useQuery({
    queryKey: ["personas"],
    queryFn: () => api.get<Persona[]>("/personas"),
  });
  const { data: models = [] } = useQuery({ queryKey: ["models"], queryFn: () => api.get<ModelInfo[]>("/models") });
  const modelNames = useMemo(() => Array.from(new Set(models.map(m => m.name))), [models]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const selected = personas.find(p => p.id === selectedId) || null;
  useEffect(() => { if (!selectedId && personas.length) setSelectedId(personas[0].id); }, [personas, selectedId]);
  useEffect(() => { setDraft(selected ? draftOf(selected) : null); setMsg(null); }, [selected]);

  const dirty = !!(selected && draft && JSON.stringify(draftOf(selected)) !== JSON.stringify(draft));

  async function run(action: () => Promise<Persona | void>, done: string) {
    setBusy(true); setMsg(null);
    try {
      const result = await action();
      await qc.invalidateQueries({ queryKey: ["personas"] });
      if (result && "id" in result) setSelectedId(result.id);
      setMsg({ ok: true, text: done });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "Не удалось сохранить" });
    } finally { setBusy(false); }
  }

  const save = () => selected && draft && run(() => api.patch<Persona>(`/personas/${selected.id}`, {
    name: draft.name.trim(),
    color: draft.color,
    instructions: draft.instructions,
    default_mode: draft.default_mode || null,
    default_model: draft.default_model || null,
    ...(selected.is_builtin ? {} : { allowed_tools: toolsOf(draft.files) }),
  }), "Сохранено");

  const create = () => run(() => api.post<Persona>("/personas", {
    name: "Новая роль", instructions: "", allowed_tools: ["files.read", "files.write"],
  }), "Роль создана — опишите её промт");

  const duplicate = () => selected && draft && run(() => api.post<Persona>("/personas", {
    name: `${draft.name} — копия`.slice(0, 120),
    color: draft.color,
    instructions: draft.instructions,
    allowed_tools: toolsOf(draft.files),
    default_mode: draft.default_mode || null,
    default_model: draft.default_model || null,
  }), "Копия создана — это своя роль, её можно менять целиком");

  const reset = async () => {
    if (!selected || !(await confirmAction(`Вернуть роли «${selected.name}» исходные название, промт и настройки?`, "Сбросить"))) return;
    run(() => api.post<Persona>(`/personas/${selected.id}/reset`), "Роль сброшена к исходной");
  };

  const remove = async () => {
    if (!selected || !(await confirmAction(`Удалить роль «${selected.name}»? Чаты с ней останутся, роль в них сбросится.`))) return;
    const id = selected.id;
    run(async () => { await api.del(`/personas/${id}`); setSelectedId(null); }, "Роль удалена");
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold">Роли</h1>
          <p className="text-sm text-neutral-500">
            Роль задаёт промт агента, доступ к файлам и умолчания чата. У встроенных ролей можно менять
            название и промт, права у них фиксированы. Свои роли настраиваются полностью.
          </p>
        </div>
        <button onClick={create} disabled={busy} className="primary-button text-xs"><Plus className="h-4 w-4" />Новая роль</button>
      </div>

      {isLoading ? <p className="text-sm text-neutral-500">Загрузка…</p> : (
        <div className="grid gap-4 md:grid-cols-[240px_minmax(0,1fr)]">
          <ul className="space-y-1">
            {personas.map(p => (
              <li key={p.id}>
                <button
                  onClick={async () => { if (!dirty || await confirmAction("Есть несохранённые изменения. Перейти без сохранения?", "Перейти")) setSelectedId(p.id); }}
                  className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm ${p.id === selectedId ? "bg-accent-500/15 text-accent-100" : "text-neutral-300 hover:bg-ink-800"}`}
                >
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: p.color || "#666" }} />
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  {p.is_builtin && <span className="shrink-0 text-[10px] text-neutral-500">встроенная</span>}
                </button>
              </li>
            ))}
          </ul>

          {selected && draft && (
            <div className="rounded-xl border border-ink-700/70 bg-ink-800/30 p-5">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <input aria-label="Цвет роли" type="color" value={draft.color} onChange={e => setDraft({ ...draft, color: e.target.value })}
                  className="h-8 w-8 shrink-0 cursor-pointer rounded border border-ink-600 bg-transparent" />
                <input aria-label="Название роли" value={draft.name} maxLength={120} onChange={e => setDraft({ ...draft, name: e.target.value })}
                  className="min-w-0 flex-1 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm" />
                {selected.hitl_required && (
                  <span title="Опасные шаги ждут подтверждения оператора" className="flex items-center gap-1 rounded bg-amber-500/15 px-2 py-1 text-[11px] text-amber-300">
                    <ShieldCheck className="h-3.5 w-3.5" />HITL
                  </span>
                )}
              </div>

              <label className="mb-1 block text-[11px] uppercase text-neutral-500">Промт роли</label>
              <textarea aria-label="Промт роли" value={draft.instructions} rows={10} maxLength={20000}
                onChange={e => setDraft({ ...draft, instructions: e.target.value })}
                placeholder="Кто этот агент, как он работает и чего избегает…"
                className="mb-4 w-full resize-y rounded-lg border border-ink-700 bg-ink-900 p-3 text-sm leading-6" />

              <div className="mb-4 grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-[11px] uppercase text-neutral-500">Режим по умолчанию</label>
                  <select aria-label="Режим по умолчанию" value={draft.default_mode}
                    onChange={e => setDraft({ ...draft, default_mode: e.target.value as Draft["default_mode"] })}
                    className="w-full rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm">
                    {MODES.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-[11px] uppercase text-neutral-500">Модель по умолчанию</label>
                  <select aria-label="Модель по умолчанию" value={draft.default_model}
                    onChange={e => setDraft({ ...draft, default_model: e.target.value })}
                    className="w-full rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm">
                    <option value="">Не менять</option>
                    {draft.default_model && !modelNames.includes(draft.default_model) && <option value={draft.default_model}>{draft.default_model} (нет среди активных)</option>}
                    {modelNames.map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                </div>
              </div>

              <label className="mb-1 block text-[11px] uppercase text-neutral-500">Доступ</label>
              {selected.is_builtin ? (
                <p className="mb-4 text-xs leading-5 text-neutral-400">
                  {(selected.allowed_tools || []).map(t => RIGHTS[t] || t).join(", ") || "без инструментов"}.
                  <span className="text-neutral-500"> Права встроенной роли фиксированы — чтобы изменить их, сделайте копию.</span>
                </p>
              ) : (
                <div role="radiogroup" aria-label="Доступ к файлам" className="mb-4 flex flex-wrap gap-1 text-xs">
                  {([["none", "Без файлов"], ["read", "Только чтение"], ["write", "Чтение и запись"]] as const).map(([id, label]) => (
                    <button key={id} role="radio" aria-checked={draft.files === id} onClick={() => setDraft({ ...draft, files: id })}
                      className={`rounded-lg px-3 py-1.5 ${draft.files === id ? "bg-accent-500/20 text-accent-100" : "bg-ink-900 text-neutral-400 hover:bg-ink-800"}`}>
                      {label}
                    </button>
                  ))}
                </div>
              )}

              {msg && <p role={msg.ok ? "status" : "alert"} className={`mb-3 text-xs ${msg.ok ? "text-emerald-300" : "text-red-300"}`}>{msg.text}</p>}
              <div className="flex flex-wrap gap-2">
                <button onClick={save} disabled={busy || !dirty || !draft.name.trim()} className="primary-button text-xs"><Save className="h-4 w-4" />Сохранить</button>
                <button onClick={duplicate} disabled={busy} className="secondary-button text-xs"><Copy className="h-4 w-4" />Дублировать</button>
                {selected.is_builtin
                  ? <button onClick={reset} disabled={busy} className="secondary-button text-xs"><RotateCcw className="h-4 w-4" />Сбросить</button>
                  : <button onClick={remove} disabled={busy} className="secondary-button text-xs hover:text-red-300"><Trash2 className="h-4 w-4" />Удалить</button>}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
