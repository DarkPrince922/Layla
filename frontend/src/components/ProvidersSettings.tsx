"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, KeyRound, Wrench, SlidersHorizontal, X } from "lucide-react";
import { api, type ProviderModel } from "@/lib/api";
import { HttpKeyBanner } from "@/components/HttpKeyBanner";

interface Provider {
  id: string;
  name: string;
  kind: string;
  base_url?: string | null;
  default_model?: string | null;
  enabled: boolean;
  active: boolean;
  has_secret: boolean;
}

const KINDS = ["openai_compatible", "anthropic", "custom"];

export function ProvidersSettings() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [insecure, setInsecure] = useState(false);
  const [ack, setAck] = useState(false);
  const [form, setForm] = useState({
    name: "",
    kind: "openai_compatible",
    base_url: "",
    default_model: "",
    api_key: "",
    active: true,
  });

  useEffect(() => {
    if (typeof window !== "undefined" && !window.isSecureContext) setInsecure(true);
  }, []);

  const { data: providers = [], isLoading } = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.get<Provider[]>("/providers"),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<Provider>("/providers", {
        name: form.name,
        kind: form.kind,
        base_url: form.base_url || null,
        default_model: form.default_model || null,
        api_key: form.api_key || null,
        active: form.active,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["providers"] });
      qc.invalidateQueries({ queryKey: ["models"] });
      setOpen(false);
      setForm({
        name: "",
        kind: "openai_compatible",
        base_url: "",
        default_model: "",
        api_key: "",
        active: true,
      });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/providers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });

  // Ввод ключа заблокирован по обычному HTTP до подтверждения (спец. §4/§7.5).
  const keyBlocked = insecure && !ack;

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Провайдеры</h1>
          <p className="text-sm text-neutral-500">
            Профили подключения LLM. Активные профили появляются в пикере модели.
            Все профили обслуживаются через прокси LiteLLM.
          </p>
        </div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 rounded-md bg-accent-600 px-3 py-1.5 text-sm text-white hover:bg-accent-500"
        >
          <Plus className="h-4 w-4" /> Добавить
        </button>
      </div>

      <div className="mb-4">
        <HttpKeyBanner />
      </div>

      {open && (
        <div className="mb-5 space-y-3 rounded-xl border border-ink-700/70 bg-ink-800/30 p-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <input
              className="rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm"
              placeholder="Название (напр. OpenAI)"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
            <select
              className="rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm"
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value })}
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            <input
              className="rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm"
              placeholder="Base URL (для OpenAI-совместимых / локальных)"
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            />
            <input
              className="rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm"
              placeholder="Модель по умолчанию"
              value={form.default_model}
              onChange={(e) => setForm({ ...form, default_model: e.target.value })}
            />
          </div>

          <div className="flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-neutral-500" />
            <input
              className="flex-1 rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm disabled:opacity-40"
              placeholder={keyBlocked ? "Ввод ключа заблокирован по обычному HTTP" : "API-ключ (хранится в зашифрованном виде)"}
              type="password"
              disabled={keyBlocked}
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            />
          </div>
          <label className="flex items-center gap-2 text-xs text-neutral-400">
            <input
              type="checkbox"
              checked={form.active}
              onChange={(e) => setForm({ ...form, active: e.target.checked })}
            />
            Активен (модель появится в пикере)
          </label>
          {insecure && (
            <label className="flex items-center gap-2 text-xs text-amber-300">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              Я понимаю, что ключи, отправленные по обычному HTTP, не шифруются при передаче.
            </label>
          )}

          <div className="flex justify-end gap-2">
            <button onClick={() => setOpen(false)} className="rounded-md px-3 py-1.5 text-sm text-neutral-400">
              Отмена
            </button>
            <button
              onClick={() => create.mutate()}
              disabled={!form.name || create.isPending}
              className="rounded-md bg-accent-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              {create.isPending ? "Сохранение…" : "Сохранить профиль"}
            </button>
          </div>
        </div>
      )}

      {isLoading ? (
        <p className="text-sm text-neutral-500">Загрузка…</p>
      ) : providers.length === 0 ? (
        <p className="rounded-lg border border-dashed border-ink-700 p-6 text-center text-sm text-neutral-500">
          Пока нет провайдеров. Добавьте хотя бы один, чтобы наполнить пикер модели.
        </p>
      ) : (
        <ul className="divide-y divide-ink-700 rounded-lg border border-ink-700">
          {providers.map((p) => (
            <li key={p.id} className="px-4 py-3">
              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium">{p.name}</span>
                    <span className="rounded bg-ink-700 px-1.5 py-0.5 text-[11px] text-neutral-400">
                      {p.kind}
                    </span>
                    {p.active && (
                      <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[11px] text-emerald-300">
                        активен
                      </span>
                    )}
                    {p.has_secret && (
                      <span className="flex items-center gap-1 text-[11px] text-neutral-500">
                        <KeyRound className="h-3 w-3" /> ключ задан
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-neutral-500">
                    {p.base_url || "—"} · {p.default_model || "нет модели по умолчанию"}
                  </div>
                </div>
                <button
                  onClick={() => remove.mutate(p.id)}
                  className="text-neutral-500 hover:text-red-400"
                  aria-label="Удалить провайдера"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
              <ProviderModels providerId={p.id} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ProviderModels({ providerId }: { providerId: string }) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);

  const { data: models = [] } = useQuery({
    queryKey: ["provider-models", providerId],
    queryFn: () => api.get<ProviderModel[]>(`/providers/${providerId}/models`),
  });

  const fetchModels = useMutation({
    mutationFn: () => api.post<ProviderModel[]>(`/providers/${providerId}/fetch-models`),
    onSuccess: (list) => {
      qc.setQueryData(["provider-models", providerId], list);
      qc.invalidateQueries({ queryKey: ["models"] });
      setMsg(`Загружено моделей: ${list.length}`);
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : "Ошибка загрузки"),
  });

  const save = useMutation({
    mutationFn: (next: ProviderModel[]) =>
      api.put(`/providers/${providerId}/models`, { models: next }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
  });

  function toggle(name: string, change: Partial<ProviderModel>) {
    const next = models.map((m) => (m.name === name ? { ...m, ...change } : m));
    qc.setQueryData(["provider-models", providerId], next);
    save.mutate(next);
  }
  async function saveSettings(name: string, change: Partial<ProviderModel>) {
    const next = models.map((m) => (m.name === name ? { ...m, ...change } : m));
    const saved = await api.put<ProviderModel[]>(`/providers/${providerId}/models`, { models: next });
    qc.setQueryData(["provider-models", providerId], saved);
    qc.invalidateQueries({ queryKey: ["models"] });
  }
  const custom = (m: ProviderModel) =>
    !!(m.max_output_manual || m.context || m.temperature != null || m.reasoning_effort || m.reasoning_budget != null);
  const current = models.find((m) => m.name === editing) || null;

  return (
    <div className="mt-2 rounded-md border border-ink-800 bg-ink-950/40 p-2">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
          Модели
        </span>
        <button
          onClick={() => fetchModels.mutate()}
          disabled={fetchModels.isPending}
          className="rounded bg-ink-700 px-2 py-0.5 text-[11px] text-neutral-200 hover:bg-ink-600 disabled:opacity-50"
        >
          {fetchModels.isPending ? "Загрузка…" : "Загрузить модели"}
        </button>
        {msg && <span className="text-[11px] text-neutral-500">{msg}</span>}
      </div>
      {models.length === 0 ? (
        <p className="text-[11px] text-neutral-600">
          Список пуст — нажмите «Загрузить модели» (используется /models провайдера).
          Пока список пуст, в пикере используется модель по умолчанию.
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {models.map((m) => (
            <label
              key={m.name}
              className={`flex cursor-pointer items-center gap-1 rounded border px-2 py-0.5 text-[11px] ${
                m.enabled
                  ? "border-accent-500/40 bg-accent-500/10 text-accent-200"
                  : "border-ink-700 bg-ink-800 text-neutral-500"
              }`}
            >
              <input
                type="checkbox"
                checked={m.enabled}
                onChange={(e) => toggle(m.name, { enabled: e.target.checked })}
                className="h-3 w-3"
              />
              {m.name}
              <button
                type="button"
                onClick={(e) => { e.preventDefault(); setEditing(editing === m.name ? null : m.name); }}
                aria-pressed={editing === m.name}
                aria-label={`Настройки модели ${m.name}`}
                title="Длина ответа, контекст, температура"
                className={`relative ml-0.5 rounded p-0.5 ${editing === m.name ? "text-white" : "text-neutral-400"}`}
              >
                <SlidersHorizontal className="h-3 w-3" />
                {custom(m) && <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-accent-300" />}
              </button>
              <button
                type="button"
                onClick={(e) => { e.preventDefault(); toggle(m.name, { tools: !m.tools }); }}
                aria-pressed={m.tools}
                aria-label={`Инструменты для ${m.name}`}
                title={m.tools
                  ? "Работа с файлами включена. Выключите, если модель не поддерживает инструменты (Layla выключит сама при отказе провайдера)."
                  : "Без инструментов: модель отвечает текстом, не трогая файлы. Нажмите, чтобы включить."}
                className={`ml-0.5 rounded p-0.5 ${m.tools ? "text-accent-300" : "text-neutral-600 line-through"}`}
              >
                <Wrench className="h-3 w-3" />
              </button>
            </label>
          ))}
        </div>
      )}
      {current && (
        <ModelSettings key={current.name} model={current} onClose={() => setEditing(null)}
          onSave={(change) => saveSettings(current.name, change)} />
      )}
    </div>
  );
}

const OUTPUT_PRESETS = [4096, 8192, 16384, 32768, 65536];
const CONTEXT_PRESETS = [32000, 128000, 200000, 1000000];
const short = (n: number) =>
  n >= 1_000_000 ? `${n / 1_000_000}M` : n % 1024 === 0 ? `${n / 1024}K` : `${Math.round(n / 1000)}K`;

/** Настройки одной модели: длина ответа, контекст, температура, глубина размышлений. */
function ModelSettings({ model, onSave, onClose }: {
  model: ProviderModel;
  onSave: (change: Partial<ProviderModel>) => Promise<void>;
  onClose: () => void;
}) {
  const [output, setOutput] = useState(model.max_output_manual && model.max_output ? String(model.max_output) : "");
  const [context, setContext] = useState(model.context ? String(model.context) : "");
  const [temperature, setTemperature] = useState(model.temperature != null ? String(model.temperature) : "");
  const [effort, setEffort] = useState(model.reasoning_effort || "");
  const [budget, setBudget] = useState(model.reasoning_budget == null ? "" : String(model.reasoning_budget));
  const [state, setState] = useState<{ busy: boolean; msg: string | null; ok: boolean }>({ busy: false, msg: null, ok: true });
  const field = "w-full rounded-md border border-ink-700 bg-ink-900 px-2 py-1.5 text-xs";
  const chip = "rounded bg-ink-800 px-1.5 py-0.5 text-[10px] text-neutral-400 hover:bg-ink-700 hover:text-neutral-200";

  async function save(reset = false) {
    const num = (v: string) => (v.trim() ? Number(v) : null);
    setState({ busy: true, msg: null, ok: true });
    try {
      await onSave({
        max_output: num(output), max_output_manual: !!output.trim(),
        context: num(context), temperature: num(temperature),
        reasoning_effort: (effort || null) as ProviderModel["reasoning_effort"],
        reasoning_budget: budget === "" ? null : Number(budget),
        reset,
      });
      setState({ busy: false, msg: reset ? "Подобранное сброшено" : "Сохранено", ok: true });
    } catch (e) {
      setState({ busy: false, msg: e instanceof Error ? e.message : "Не удалось сохранить", ok: false });
    }
  }

  return (
    <div className="mt-2 rounded-md border border-ink-700 bg-ink-900/60 p-3">
      <div className="mb-3 flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-neutral-200">{model.name}</span>
        <button onClick={onClose} aria-label="Закрыть настройки модели" className="rounded p-1 text-neutral-500 hover:bg-ink-800"><X className="h-3.5 w-3.5" /></button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-[11px] text-neutral-400">Макс. токенов ответа</span>
          <input type="number" min={256} max={1000000} value={output} onChange={(e) => setOutput(e.target.value)}
            placeholder={model.max_output && !model.max_output_manual ? `Авто · сейчас ${model.max_output}` : "Авто"} className={field} />
          <span className="mt-1 flex flex-wrap gap-1">
            {OUTPUT_PRESETS.map((n) => <button key={n} type="button" className={chip} onClick={() => setOutput(String(n))}>{short(n)}</button>)}
            <button type="button" className={chip} onClick={() => setOutput("")}>Авто</button>
          </span>
          <span className="mt-1 block text-[10px] leading-4 text-neutral-500">«Авто» — Layla сама поднимет лимит, если ответ не влезет. Число — жёсткий потолок.</span>
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-neutral-400">Контекст модели, токенов</span>
          <input type="number" min={1024} max={10000000} value={context} onChange={(e) => setContext(e.target.value)}
            placeholder="Не ограничивать" className={field} />
          <span className="mt-1 flex flex-wrap gap-1">
            {CONTEXT_PRESETS.map((n) => <button key={n} type="button" className={chip} onClick={() => setContext(String(n))}>{short(n)}</button>)}
            <button type="button" className={chip} onClick={() => setContext("")}>Без ограничения</button>
          </span>
          <span className="mt-1 block text-[10px] leading-4 text-neutral-500">Длинная история сворачивается в сводку: старое — кратко, свежие сообщения — целиком, в чате всё остаётся. Если провайдер сообщит предел — подставится сам.</span>
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-neutral-400">Температура</span>
          <input type="number" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(e.target.value)}
            placeholder="По умолчанию" className={field} />
          <span className="mt-1 block text-[10px] leading-4 text-neutral-500">0 — строже и предсказуемее, 1+ — свободнее. Пусто — как решит провайдер.</span>
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-neutral-400">Глубина размышлений</span>
          <select value={effort} onChange={(e) => setEffort(e.target.value)} className={field}>
            <option value="">По умолчанию</option>
            <option value="low">Низкая (low)</option>
            <option value="medium">Средняя (medium)</option>
            <option value="high">Высокая (high)</option>
          </select>
          <span className="mt-1 block text-[10px] leading-4 text-neutral-500">Для reasoning-моделей (o-серия, gpt-5, DeepSeek R1 через совместимые API). Остальные модели параметр игнорируют.</span>
        </label>
        <label className="block sm:col-span-2">
          <span className="mb-1 block text-[11px] text-neutral-400">Бюджет размышлений на шаг</span>
          <select value={budget} onChange={(e) => setBudget(e.target.value)} className={field}>
            <option value="">Авто (6K токенов)</option>
            <option value="2048">2K — очень коротко</option>
            <option value="4096">4K</option>
            <option value="8192">8K</option>
            <option value="16384">16K</option>
            <option value="32768">32K — сложные задачи</option>
            <option value="0">Без ограничения</option>
          </select>
          <span className="mt-1 block text-[10px] leading-4 text-neutral-500">
            Если модель думает дольше и ещё не начала действовать (обычно пишет весь код в «мыслях»), Layla остановит
            размышления и попросит коротко спланировать шаг и действовать. OpenRouter и Qwen получают бюджет напрямую.
          </span>
        </label>
      </div>
      {!!model.dropped?.length && (
        <p className="mt-3 text-[11px] text-amber-300">Провайдер не принял: {model.dropped.join(", ")} — отправляется без этого.</p>
      )}
      {state.msg && <p role={state.ok ? "status" : "alert"} className={`mt-2 text-[11px] ${state.ok ? "text-emerald-300" : "text-red-300"}`}>{state.msg}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={() => save()} disabled={state.busy} className="rounded bg-accent-600 px-3 py-1 text-xs text-white hover:bg-accent-500 disabled:opacity-50">Сохранить</button>
        <button onClick={() => save(true)} disabled={state.busy} title="Забыть, что Layla подобрала сама (лимиты, отключённые параметры)"
          className="rounded bg-ink-700 px-3 py-1 text-xs text-neutral-200 hover:bg-ink-600 disabled:opacity-50">Сбросить подобранное</button>
      </div>
    </div>
  );
}
