"use client";

import { useState } from "react";
import { useLocale } from "@/store/locale";
import { useLayout } from "@/store/layout";
import { type Locale } from "@/lib/i18n";
import { api, type Me } from "@/lib/api";
import { useAuth } from "@/store/auth";

function ChangePassword() {
  const fetchMe = useAuth((s) => s.fetchMe);
  const mustChange = useAuth((s) => s.user?.must_change_password);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setErr(null);
    setMsg(null);
    setBusy(true);
    try {
      await api.post<Me>("/auth/change-password", {
        current_password: current,
        new_password: next,
      });
      setMsg("Пароль изменён.");
      setCurrent("");
      setNext("");
      await fetchMe();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось изменить пароль");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-6 border-t border-ink-700 pt-6">
      <div className="mb-1 text-sm font-semibold">Смена пароля</div>
      {mustChange && (
        <p className="mb-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-300">
          Пароль был выдан администратором — рекомендуем сменить его сейчас.
        </p>
      )}
      <div className="grid max-w-sm grid-cols-1 gap-2">
        <input
          type="password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          placeholder="Текущий пароль"
          className="rounded-md border border-ink-700 bg-ink-800 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500"
        />
        <input
          type="password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          placeholder="Новый пароль (мин. 8 символов)"
          className="rounded-md border border-ink-700 bg-ink-800 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500"
        />
      </div>
      {err && <p className="mt-2 text-xs text-red-400">{err}</p>}
      {msg && <p className="mt-2 text-xs text-emerald-400">{msg}</p>}
      <button
        onClick={submit}
        disabled={busy || current.length < 1 || next.length < 8}
        className="mt-3 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
      >
        {busy ? "Сохранение…" : "Изменить пароль"}
      </button>
    </section>
  );
}

export function GeneralSettings() {
  const { locale, setLocale, t } = useLocale();
  const { mode, setMode } = useLayout();

  return (
    <div className="max-w-xl">
      <h1 className="text-xl font-semibold">{t("general.title")}</h1>
      <p className="mb-5 text-sm text-neutral-500">
        Язык, раскладка и папка проектов.
      </p>

      <section className="mb-6">
        <div className="mb-1 text-sm font-semibold">{t("general.language")}</div>
        <div className="flex gap-1">
          {(["ru", "en"] as Locale[]).map((l) => (
            <button
              key={l}
              onClick={() => setLocale(l)}
              className={`rounded px-3 py-1.5 text-xs uppercase ${
                locale === l ? "bg-indigo-600 text-white" : "bg-ink-800 text-neutral-400"
              }`}
            >
              {l}
            </button>
          ))}
        </div>
      </section>

      <section className="mb-6">
        <div className="mb-1 text-sm font-semibold">{t("general.layout")}</div>
        <div className="flex gap-1">
          {(["desktop", "mobile"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`rounded px-3 py-1.5 text-xs ${
                mode === m ? "bg-indigo-600 text-white" : "bg-ink-800 text-neutral-400"
              }`}
            >
              {t(`layout.${m}`)}
            </button>
          ))}
        </div>
      </section>

      <section>
        <div className="mb-1 text-sm font-semibold">{t("general.projectsDir")}</div>
        <input
          readOnly
          value="./data/projects"
          className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs text-neutral-400"
        />
        <p className="mt-1 text-[11px] text-neutral-600">
          Каталог, куда клонируются репозитории (задаётся в .env: LAYLA_PROJECTS_DIR).
        </p>
      </section>

      <ChangePassword />
    </div>
  );
}
