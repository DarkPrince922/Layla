"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Palette, Sparkles, Monitor, Tablet, Smartphone, Code2, Eye } from "lucide-react";
import { api, type Design, type ModelInfo, type Job } from "@/lib/api";

const STACKS = ["html", "react", "vue"];
const ARTIFACTS = ["Landing", "Dashboard", "Pricing", "Mobile app", "Email", "Editorial", "Slides"];
const DIRECTIONS = ["Editorial", "Modern minimal", "Tech utility", "Brutalist", "Soft warm"];
const THEMES = ["light", "dark", "both"];
const PAGES = ["Single", "Multi"];

const BREAKPOINTS = {
  desktop: "100%",
  tablet: "768px",
  mobile: "390px",
} as const;

export function DesignDomain() {
  const qc = useQueryClient();
  const latest = useRef<string>();
  const [brief, setBrief] = useState({
    artifact_type: "Landing",
    direction: "Modern minimal",
    tone: "",
    theme: "both",
    pages: "Single",
    reference: "",
    brand: "",
    notes: "",
  });
  const [stack, setStack] = useState("html");
  const [model, setModel] = useState("");
  const [active, setActive] = useState<Design | null>(null);
  const [view, setView] = useState<"preview" | "code">("preview");
  const [bp, setBp] = useState<keyof typeof BREAKPOINTS>("desktop");
  const [showBrief, setShowBrief] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { data: models = [] } = useQuery({
    queryKey: ["models"],
    queryFn: () => api.get<ModelInfo[]>("/models"),
  });
  const { data: designs = [] } = useQuery({
    queryKey: ["designs"],
    queryFn: () => api.get<Design[]>("/designs"),
  });

  const generate = useMutation({
    mutationFn: () =>
      api.post<Job>("/designs/generate", { stack, brief, model: model || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["designs"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setShowBrief(false);
      setError(null);
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Ошибка генерации"),
  });

  useEffect(() => {
    if (designs.length && latest.current !== designs[0].id) {
      const linked = !latest.current && new URLSearchParams(window.location.search).get("design");
      latest.current = designs[0].id;
      setActive(designs.find(d => d.id === linked) || designs[0]); setShowBrief(false);
    }
  }, [designs]);
  const html = active?.files?.[0]?.content ?? "";

  return (
    <div className="domain-workspace flex h-full min-w-0 flex-col">
      <header className="workspace-toolbar"><span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-500/10 text-accent-300"><Palette className="h-5 w-5" /></span><div className="workspace-title"><h1>Дизайн</h1><p>Превратите идею в интерфейс</p></div><button className="secondary-button text-xs" onClick={() => setShowBrief(!showBrief)}>{showBrief ? "Результат" : "Изменить бриф"}</button></header>
      <div className="domain-columns" data-detail={!showBrief}>
      {/* Левая колонка: бриф */}
      <div className="domain-list p-5">
        <h2 className="mb-3 text-sm font-semibold">Бриф дизайна</h2>

        <label className="mb-1 block text-[11px] uppercase text-neutral-500">Стек</label>
        <div className="mb-3 flex gap-1">
          {STACKS.map((s) => (
            <button
              key={s}
              onClick={() => setStack(s)}
              className={`flex-1 rounded px-2 py-1 text-xs ${
                stack === s ? "bg-accent-600 text-white" : "bg-ink-800 text-neutral-400"
              }`}
            >
              {s === "html" ? "Plain HTML" : s === "react" ? "React" : "Vue"}
            </button>
          ))}
        </div>

        <Field label="Тип артефакта">
          <select
            value={brief.artifact_type}
            onChange={(e) => setBrief({ ...brief, artifact_type: e.target.value })}
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
          >
            {ARTIFACTS.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </Field>

        <Field label="Направление">
          <select
            value={brief.direction}
            onChange={(e) => setBrief({ ...brief, direction: e.target.value })}
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
          >
            {DIRECTIONS.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </Field>

        <div className="grid grid-cols-2 gap-2">
          <Field label="Тема">
            <select
              value={brief.theme}
              onChange={(e) => setBrief({ ...brief, theme: e.target.value })}
              className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
            >
              {THEMES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </Field>
          <Field label="Страницы">
            <select
              value={brief.pages}
              onChange={(e) => setBrief({ ...brief, pages: e.target.value })}
              className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
            >
              {PAGES.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="Тон">
          <input
            value={brief.tone}
            onChange={(e) => setBrief({ ...brief, tone: e.target.value })}
            placeholder="напр. дружелюбный, деловой"
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
          />
        </Field>
        <Field label="Референс (URL)">
          <input
            value={brief.reference}
            onChange={(e) => setBrief({ ...brief, reference: e.target.value })}
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
          />
        </Field>
        <Field label="Доп. требования">
          <textarea
            value={brief.notes}
            onChange={(e) => setBrief({ ...brief, notes: e.target.value })}
            rows={3}
            className="w-full resize-none rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
          />
        </Field>

        <div className="mb-3 mt-1">
          <label className="mb-1 block text-[11px] uppercase text-neutral-500">Модель</label>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
          >
            {models.length === 0 && <option value="">Нет активных моделей</option>}
            {models.map((m) => (
              <option key={m.provider_id} value={m.name}>
                {m.name}
              </option>
            ))}
          </select>
        </div>

        {error && <p className="mb-2 text-xs text-red-400">{error}</p>}

        <button
          onClick={() => generate.mutate()}
          disabled={generate.isPending}
          className="flex items-center justify-center gap-1.5 rounded-md bg-accent-600 px-3 py-2 text-sm text-white hover:bg-accent-500 disabled:opacity-50"
        >
          <Sparkles className="h-4 w-4" />
          {generate.isPending ? "Генерация…" : "Сгенерировать"}
        </button>

        {designs.length > 0 && (
          <div className="mt-4">
            <div className="mb-1 text-[11px] uppercase text-neutral-500">История</div>
            <div className="space-y-1">
              {designs.map((d) => (
                <button
                  key={d.id}
                  onClick={() => { setActive(d); setShowBrief(false); }}
                  className={`block w-full truncate rounded px-2 py-1 text-left text-xs ${
                    active?.id === d.id ? "bg-ink-700 text-white" : "text-neutral-400 hover:bg-ink-800"
                  }`}
                >
                  {String((d.brief as Record<string, string>)?.artifact_type || "Design")} · {d.stack}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Правая часть: превью / код */}
      <div className="domain-detail">
        <button className="domain-back items-center gap-2 px-4 py-3 text-sm text-neutral-400" onClick={() => setShowBrief(true)}><ArrowLeft className="h-4 w-4" />К брифу</button>
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-700/60 px-4 py-3">
          <div className="flex gap-1">
            <ToolbarBtn active={view === "preview"} onClick={() => setView("preview")}>
              <Eye className="h-3.5 w-3.5" /> Превью
            </ToolbarBtn>
            <ToolbarBtn active={view === "code"} onClick={() => setView("code")}>
              <Code2 className="h-3.5 w-3.5" /> Код
            </ToolbarBtn>
          </div>
          {view === "preview" && (
            <div className="ml-auto flex gap-1">
              <ToolbarBtn label="Компьютер" active={bp === "desktop"} onClick={() => setBp("desktop")}>
                <Monitor className="h-3.5 w-3.5" />
              </ToolbarBtn>
              <ToolbarBtn label="Планшет" active={bp === "tablet"} onClick={() => setBp("tablet")}>
                <Tablet className="h-3.5 w-3.5" />
              </ToolbarBtn>
              <ToolbarBtn label="Телефон" active={bp === "mobile"} onClick={() => setBp("mobile")}>
                <Smartphone className="h-3.5 w-3.5" />
              </ToolbarBtn>
            </div>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-auto bg-ink-950/40 p-4">
          {!active ? (
            <div className="grid h-full place-items-center p-4 text-center"><div className="max-w-sm"><span className="empty-orb"><Palette className="h-6 w-6" /></span><h2 className="text-xl font-semibold">Каким будет ваш следующий проект?</h2><p className="mt-3 text-sm leading-7 text-neutral-400">Выберите стиль и расскажите об идее. Здесь появится готовый интерфейс.</p></div></div>
          ) : view === "preview" ? (
            <div className="mx-auto h-full bg-white" style={{ width: BREAKPOINTS[bp], maxWidth: "100%" }}>
              {/* Изолированный sandbox: скрипты выполняются, доступа к родителю нет. */}
              <iframe
                title="preview"
                sandbox="allow-scripts"
                srcDoc={html}
                className="h-full w-full border-0"
              />
            </div>
          ) : (
            <pre className="whitespace-pre-wrap text-xs leading-relaxed text-neutral-300">
              <code>{html}</code>
            </pre>
          )}
        </div>
      </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <label className="mb-1 block text-[11px] uppercase text-neutral-500">{label}</label>
      {children}
    </div>
  );
}

function ToolbarBtn({
  active,
  onClick,
  children,
  label,
}: {
  active: boolean;
  label?: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${
        active ? "bg-ink-700 text-white" : "text-neutral-400 hover:bg-ink-800"
      }`}
    >
      {children}
    </button>
  );
}
