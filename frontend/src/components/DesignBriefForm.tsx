"use client";

import { ChevronDown, Dices, RotateCcw, Sparkles } from "lucide-react";
import type { ModelInfo } from "@/lib/api";
import {
  ANIMATION, ARTIFACTS, CONTENT, CREATIVITY, CSS_MODES, DEFAULT_BRIEF, DENSITY, DEVICES, DIRECTIONS, EFFECTS,
  FONTS, ICONS, IMAGERY, INTERACTIVITY, LANGUAGES, LAYOUTS, PAGES, PALETTES, RADIUS, SECTIONS, STACKS, THEMES,
  TONES, configured, paletteValue, surprise, type Brief,
} from "@/lib/design-brief";

const INPUT = "w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs";

type Props = {
  brief: Brief;
  onChange: (brief: Brief) => void;
  stack: string;
  onStack: (stack: string) => void;
  model: string;
  onModel: (model: string) => void;
  models: ModelInfo[];
  pending: boolean;
  onGenerate: () => void;
};

export function DesignBriefForm({ brief, onChange, stack, onStack, model, onModel, models, pending, onGenerate }: Props) {
  const set = <K extends keyof Brief>(key: K, value: Brief[K]) => onChange({ ...brief, [key]: value });
  const toggle = (key: "effects" | "sections", value: string) =>
    set(key, brief[key].includes(value) ? brief[key].filter(v => v !== value) : [...brief[key], value]);
  const tones = brief.tone ? brief.tone.split(",").map(t => t.trim()).filter(Boolean) : [];
  const toggleTone = (value: string) =>
    set("tone", (tones.includes(value) ? tones.filter(t => t !== value) : [...tones, value]).join(", "));
  const creativity = CREATIVITY.find(c => c.id === brief.creativity) || CREATIVITY[1];

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
      <div className="mb-3 flex gap-2">
        <button type="button" onClick={() => onChange(surprise(brief))} className="secondary-button flex-1 justify-center text-xs"
          title="Случайный стиль: направление, палитра, шрифты, эффекты. Тип, бренд и секции не меняются">
          <Dices className="h-4 w-4" />Удиви меня
        </button>
        <button type="button" onClick={() => onChange({ ...DEFAULT_BRIEF })} className="secondary-button text-xs" title="Вернуть настройки по умолчанию">
          <RotateCcw className="h-4 w-4" />Сбросить
        </button>
      </div>

      <Group title="Основное" open count={configured(brief, ["brand", "industry", "audience", "language"])}>
        <Label>Стек</Label>
        <Segmented value={stack} onChange={onStack} options={STACKS} />
        <Field label="Тип артефакта">
          <Select value={brief.artifact_type} onChange={v => set("artifact_type", v)} options={withCurrent(ARTIFACTS, brief.artifact_type)} auto={false} />
        </Field>
        <Field label="Бренд / название">
          <input value={brief.brand} onChange={e => set("brand", e.target.value)} maxLength={200} placeholder="напр. Studio Nord" className={INPUT} />
        </Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Сфера">
            <input value={brief.industry} onChange={e => set("industry", e.target.value)} maxLength={200} placeholder="кофейня, финтех…" className={INPUT} />
          </Field>
          <Field label="Аудитория">
            <input value={brief.audience} onChange={e => set("audience", e.target.value)} maxLength={200} placeholder="кто пользователи" className={INPUT} />
          </Field>
        </div>
        <Field label="Язык текстов">
          <Select value={brief.language} onChange={v => set("language", v)} options={withCurrent(LANGUAGES, brief.language)} auto={false} />
        </Field>
      </Group>

      <Group title="Стиль" count={configured(brief, ["direction", "tone", "theme", "layout", "density", "radius", "effects"])}>
        <Field label="Направление">
          <Select value={brief.direction} onChange={v => set("direction", v)} options={withCurrent(DIRECTIONS, brief.direction)} auto={false} />
        </Field>
        <Label>Тон</Label>
        <Chips options={TONES} selected={tones} onToggle={toggleTone} />
        <Label>Тема</Label>
        <Segmented value={brief.theme} onChange={v => set("theme", v)} options={THEMES} />
        <Field label="Композиция">
          <Select value={brief.layout} onChange={v => set("layout", v)} options={LAYOUTS} />
        </Field>
        <Label>Плотность</Label>
        <Segmented value={brief.density} onChange={v => set("density", v)} options={auto(DENSITY)} />
        <Label>Скругления</Label>
        <Segmented value={brief.radius} onChange={v => set("radius", v)} options={auto(RADIUS)} />
        <Label>Эффекты</Label>
        <Chips options={EFFECTS} selected={brief.effects} onToggle={v => toggle("effects", v)} />
      </Group>

      <Group title="Цвет и шрифты" count={configured(brief, ["palette", "accent_color", "fonts"])}>
        <Label>Палитра</Label>
        <div className="mb-3 grid grid-cols-3 gap-1.5">
          <button type="button" onClick={() => set("palette", "")} aria-pressed={!brief.palette}
            className={`rounded-md border px-2 py-1.5 text-[11px] ${!brief.palette ? "border-accent-400 text-white" : "border-ink-700 text-neutral-400 hover:border-ink-500"}`}>
            Авто
          </button>
          {PALETTES.map(p => {
            const value = paletteValue(p);
            const active = brief.palette === value;
            return (
              <button key={p.name} type="button" onClick={() => set("palette", active ? "" : value)} aria-pressed={active} title={value}
                className={`rounded-md border p-1 text-left ${active ? "border-accent-400" : "border-ink-700 hover:border-ink-500"}`}>
                <span className="flex h-3 overflow-hidden rounded-sm">
                  {p.colors.map(c => <span key={c} className="flex-1" style={{ background: c }} />)}
                </span>
                <span className="mt-1 block truncate text-[10px] text-neutral-400">{p.name}</span>
              </button>
            );
          })}
        </div>
        <Label>Акцентный цвет</Label>
        <div className="mb-3 flex items-center gap-2">
          <input type="color" aria-label="Акцентный цвет" value={brief.accent_color || "#7c5cff"}
            onChange={e => set("accent_color", e.target.value.toUpperCase())}
            className="h-8 w-10 cursor-pointer rounded border border-ink-700 bg-ink-800 p-0.5" />
          <span className="flex-1 text-xs text-neutral-400">{brief.accent_color || "Авто — подберёт модель"}</span>
          {brief.accent_color && <button type="button" onClick={() => set("accent_color", "")} className="text-xs text-neutral-400 hover:text-white">Авто</button>}
        </div>
        <Field label="Шрифты">
          <Select value={brief.fonts} onChange={v => set("fonts", v)} options={FONTS} />
        </Field>
      </Group>

      <Group title="Контент" count={configured(brief, ["pages", "sections", "content", "imagery", "icons"])}>
        <Label>Страницы</Label>
        <Segmented value={brief.pages} onChange={v => set("pages", v)} options={PAGES} />
        <Label>Секции {brief.sections.length ? `· ${brief.sections.length}` : "· авто"}</Label>
        <Chips options={SECTIONS} selected={brief.sections} onToggle={v => toggle("sections", v)} ordered />
        <Label>Наполнение</Label>
        <Segmented value={brief.content} onChange={v => set("content", v)} options={auto(CONTENT, ["Мало", "Средне", "Много"])} />
        <div className="grid grid-cols-2 gap-2">
          <Field label="Изображения">
            <Select value={brief.imagery} onChange={v => set("imagery", v)} options={IMAGERY} />
          </Field>
          <Field label="Иконки">
            <Select value={brief.icons} onChange={v => set("icons", v)} options={ICONS} />
          </Field>
        </div>
      </Group>

      <Group title="Поведение и техника" count={configured(brief, ["animation", "interactivity", "device", "css", "accessibility"])}>
        <Field label="Анимации">
          <Select value={brief.animation} onChange={v => set("animation", v)} options={ANIMATION} />
        </Field>
        <Field label="Интерактивность">
          <Select value={brief.interactivity} onChange={v => set("interactivity", v)} options={INTERACTIVITY} />
        </Field>
        <Label>Устройства</Label>
        <Segmented value={brief.device} onChange={v => set("device", v)} options={auto(DEVICES, ["Все", "Мобильные", "Десктоп"])} />
        <Label>CSS</Label>
        <Segmented value={brief.css} onChange={v => set("css", v)} options={CSS_MODES} />
        <label className="mb-1 flex cursor-pointer items-center gap-2 text-xs text-neutral-300">
          <input type="checkbox" checked={brief.accessibility} onChange={e => set("accessibility", e.target.checked)} className="accent-accent-500" />
          Доступность WCAG AA: контраст, клавиатура, aria
        </label>
      </Group>

      <div className="mb-3 rounded-lg border border-ink-700/70 p-3">
        <Label>Креативность</Label>
        <Segmented value={brief.creativity} onChange={v => set("creativity", v as Brief["creativity"])}
          options={CREATIVITY.map(c => [c.id, c.label] as const)} />
        <p className="-mt-1 text-[11px] leading-4 text-neutral-500">{creativity.hint}</p>
      </div>

      <Field label="Референс (URL)">
        <input value={brief.reference} onChange={e => set("reference", e.target.value)} maxLength={1000} className={INPUT} />
      </Field>
      <Field label="Доп. требования">
        <textarea value={brief.notes} onChange={e => set("notes", e.target.value)} rows={3} maxLength={6000}
          placeholder="Что обязательно должно быть: блоки, тексты, ограничения" className={`${INPUT} resize-none`} />
      </Field>
      <Field label="Модель">
        <select value={model} onChange={e => onModel(e.target.value)} className={INPUT}>
          {models.length === 0 && <option value="">Нет активных моделей</option>}
          {models.map(m => <option key={`${m.provider_id}|${m.name}`} value={m.name}>{m.name} · {m.provider}</option>)}
        </select>
        <p className="mt-1 text-[11px] leading-4 text-neutral-500">
          Длина ответа, температура и глубина размышлений берутся из «Настройки → Провайдеры → модель».
        </p>
      </Field>
      <button onClick={onGenerate} disabled={pending} className="flex w-full items-center justify-center gap-1.5 rounded-md bg-accent-600 px-3 py-2 text-sm text-white hover:bg-accent-500 disabled:opacity-50">
        <Sparkles className="h-4 w-4" />{pending ? "Отправляю…" : "Сгенерировать макет"}
      </button>
      <p className="pt-2 text-[11px] leading-5 text-neutral-500">
        Справа видно, как модель думает и пишет код, превью обновляется по ходу. Генерация идёт в фоне — можно уйти в другой раздел.
      </p>
    </div>
  );
}

/** Значение из старой версии или «Удиви меня», которого нет в списке, тоже должно быть видно. */
function withCurrent(list: string[], value: string) {
  return value && !list.includes(value) ? [value, ...list] : list;
}

function auto(values: string[], labels?: string[]) {
  return [["", "Авто"] as const, ...values.map((v, i) => [v, labels?.[i] ?? v] as const)];
}

function Group({ title, count, open, children }: { title: string; count: number; open?: boolean; children: React.ReactNode }) {
  return (
    <details open={open} className="group mb-3 rounded-lg border border-ink-700/70">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-neutral-200 [&::-webkit-details-marker]:hidden">
        <span className="flex-1">{title}</span>
        {count > 0 && <span className="rounded-full bg-accent-500/15 px-1.5 text-[10px] text-accent-200">{count}</span>}
        <ChevronDown className="h-3.5 w-3.5 text-neutral-500 transition-transform group-open:rotate-180" />
      </summary>
      <div className="px-3 pb-1">{children}</div>
    </details>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <p className="mb-1 block text-[11px] uppercase text-neutral-500">{children}</p>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <label className="mb-1 block text-[11px] uppercase text-neutral-500">{label}</label>
      {children}
    </div>
  );
}

function Select({ value, onChange, options, auto = true }: {
  value: string; onChange: (v: string) => void; options: string[]; auto?: boolean;
}) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)} className={INPUT}>
      {auto && <option value="">Авто</option>}
      {options.map(o => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function Segmented({ value, onChange, options }: {
  value: string; onChange: (v: string) => void; options: readonly (readonly [string, string])[];
}) {
  return (
    <div className="mb-3 flex flex-wrap gap-1">
      {options.map(([id, label]) => (
        <button key={id || "auto"} type="button" onClick={() => onChange(id)} aria-pressed={value === id}
          className={`min-w-0 flex-1 truncate rounded px-2 py-1 text-xs ${value === id ? "bg-accent-600 text-white" : "bg-ink-800 text-neutral-400 hover:text-neutral-200"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

function Chips({ options, selected, onToggle, ordered }: {
  options: string[]; selected: string[]; onToggle: (v: string) => void; ordered?: boolean;
}) {
  return (
    <div className="mb-3 flex flex-wrap gap-1">
      {options.map(o => {
        const index = selected.indexOf(o);
        return (
          <button key={o} type="button" onClick={() => onToggle(o)} aria-pressed={index >= 0}
            className={`rounded-full border px-2 py-0.5 text-[11px] ${index >= 0 ? "border-accent-400 bg-accent-500/15 text-accent-100" : "border-ink-700 text-neutral-400 hover:border-ink-500"}`}>
            {ordered && index >= 0 ? `${index + 1}. ` : ""}{o}
          </button>
        );
      })}
    </div>
  );
}
