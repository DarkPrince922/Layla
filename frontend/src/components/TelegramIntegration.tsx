"use client";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type TelegramConfig } from "@/lib/api";

const defaults = { enabled: true, control_enabled: true, notify_done: true, notify_error: true,
  notify_cancelled: true, notify_approval: true, public_url: "", default_chat_id: "" };

export function TelegramIntegration() {
  const qc = useQueryClient();
  const [token, setToken] = useState("");
  const [form, setForm] = useState(defaults);
  const [ack, setAck] = useState(false);
  const [insecure, setInsecure] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pairUrl, setPairUrl] = useState<string | null>(null);
  const { data: cfg } = useQuery({ queryKey: ["telegram"], queryFn: () => api.get<TelegramConfig>("/integrations/telegram"),
    refetchInterval: pairUrl ? 3000 : false });
  useEffect(() => { setInsecure(!window.isSecureContext); }, []);
  useEffect(() => {
    if (cfg?.configured) setForm({ enabled: cfg.enabled, control_enabled: cfg.control_enabled,
      notify_done: cfg.notify_done, notify_error: cfg.notify_error, notify_cancelled: cfg.notify_cancelled,
      notify_approval: cfg.notify_approval, public_url: cfg.public_url ?? "", default_chat_id: cfg.default_chat_id ?? "" });
    if (cfg?.connected) setPairUrl(null);
  }, [cfg]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["telegram"] });
  const failure = (e: Error) => setError(e.message);
  const save = useMutation({ mutationFn: () => api.put("/integrations/telegram", { ...form,
    public_url: form.public_url || null, default_chat_id: form.default_chat_id || null, bot_token: token || undefined }),
    onSuccess: () => { setToken(""); setPairUrl(null); setError(""); setMessage("Настройки сохранены"); refresh(); }, onError: failure });
  const pair = useMutation({ mutationFn: () => api.post<{ url: string; expires_in: number }>("/integrations/telegram/pair"),
    onSuccess: result => { setError(""); setPairUrl(result.url); setMessage("Откройте бота и нажмите Start. Ссылка действует 10 минут."); refresh(); }, onError: failure });
  const disconnect = useMutation({ mutationFn: () => api.post("/integrations/telegram/disconnect"),
    onSuccess: () => { setPairUrl(null); setMessage("Telegram-аккаунт отвязан"); refresh(); }, onError: failure });
  const test = useMutation({ mutationFn: () => api.post("/integrations/telegram/test", {}),
    onSuccess: () => { setError(""); setMessage("Тестовое сообщение отправлено"); }, onError: failure });
  const remove = useMutation({ mutationFn: () => api.del("/integrations/telegram"),
    onSuccess: () => { setPairUrl(null); setForm(defaults); setMessage("Подключение удалено"); refresh(); }, onError: failure });
  const busy = save.isPending || pair.isPending || disconnect.isPending || remove.isPending;
  const field = "w-full rounded-md border border-ink-700 bg-ink-900 px-3 py-2 text-sm disabled:opacity-40";
  return <section className="rounded-xl border border-ink-700 bg-ink-800/30 p-5">
    <h2 className="text-sm font-semibold text-neutral-300">Telegram — управление Лейлой</h2>
    <p className="mt-2 text-xs text-neutral-400">Разделы, сайты и проекты, модели, задачи, результаты, остановка и подтверждения действий. Уведомления приходят и для работы, начатой в веб-интерфейсе.</p>
    <ol className="my-3 list-inside list-decimal space-y-1 text-xs text-neutral-400">
      <li>Создайте отдельного бота у @BotFather и сохраните токен.</li>
      <li>Нажмите «Подключить Telegram», откройте ссылку и нажмите Start.</li>
      <li>Управляйте через /menu. Для полного интерфейса укажите HTTPS-адрес Лейлы.</li>
    </ol>
    {cfg?.configured && <p className="mb-3 text-xs text-emerald-300">{cfg.connected ? "Аккаунт привязан" : "Ожидается привязка аккаунта"} · {cfg.bot_username ? "@" + cfg.bot_username : cfg.token_masked}</p>}
    <div className="space-y-3">
      <label className="block text-xs text-neutral-400">Токен бота
        <input aria-label="Токен Telegram-бота" type="password" autoComplete="off" maxLength={512} value={token} onChange={e => setToken(e.target.value)}
          disabled={busy || (insecure && !ack)} placeholder={cfg?.configured ? "Оставьте пустым, чтобы сохранить текущий токен" : "Токен от @BotFather"} className={field} />
      </label>
      {insecure && <label className="flex gap-2 text-xs text-amber-300"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />Понимаю риск передачи токена по HTTP.</label>}
      <label className="block text-xs text-neutral-400">HTTPS-адрес Лейлы
        <input aria-label="Адрес Лейлы" value={form.public_url} onChange={e => setForm({ ...form, public_url: e.target.value })} placeholder="https://layla.example.com" className={field} />
      </label>
      <label className="block text-xs text-neutral-400">Чат для уведомлений (необязательно)
        <input aria-label="Чат уведомлений" value={form.default_chat_id} onChange={e => setForm({ ...form, default_chat_id: e.target.value })} placeholder="Заполнится автоматически после привязки" className={field} />
      </label>
      <div className="grid gap-2 text-xs text-neutral-300 sm:grid-cols-2">
        {([['enabled', 'Интеграция включена'], ['control_enabled', 'Управление через бота'], ['notify_done', 'Работа завершена'],
          ['notify_error', 'Ошибки'], ['notify_cancelled', 'Остановленные задачи'], ['notify_approval', 'Запросы подтверждения']] as const).map(([key, label]) =>
          <label key={key} className="flex items-center gap-2"><input type="checkbox" checked={form[key]} onChange={e => setForm({ ...form, [key]: e.target.checked })} />{label}</label>)}
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="primary-button text-xs" disabled={busy || (!cfg?.configured && !token) || (!!token && insecure && !ack)} onClick={() => save.mutate()}>Сохранить</button>
        {cfg?.configured && <>
          <button className="secondary-button text-xs" disabled={busy || !cfg.enabled} onClick={() => pair.mutate()}>{cfg.connected ? "Перепривязать Telegram" : "Подключить Telegram"}</button>
          <button className="secondary-button text-xs" disabled={test.isPending} onClick={() => test.mutate()}>Тест уведомления</button>
          {cfg.connected && <button className="secondary-button text-xs" disabled={busy} onClick={() => disconnect.mutate()}>Отвязать аккаунт</button>}
          <button className="secondary-button text-xs" disabled={busy} onClick={() => { if (window.confirm("Удалить Telegram-подключение и сохранённый токен?")) remove.mutate(); }}>Удалить</button>
        </>}
      </div>
      {pairUrl && <a href={pairUrl} target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-accent-600 p-3 text-sm text-accent-300 underline">Открыть бота и привязать аккаунт</a>}
      {message && <p role="status" className="text-xs text-neutral-300">{message}</p>}
      {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
      {cfg?.last_error && <p className="text-xs text-amber-300">Последняя ошибка бота: {cfg.last_error}</p>}
    </div>
  </section>;
}
