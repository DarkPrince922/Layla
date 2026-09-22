"use client";

import { useLocale } from "@/store/locale";
import { useLayout } from "@/store/layout";
import { type Locale } from "@/lib/i18n";

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
          value="/workspace/projects"
          className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs text-neutral-400"
        />
        <p className="mt-1 text-[11px] text-neutral-600">
          Каталог, куда клонируются репозитории (задаётся в .env: LAYLA_PROJECTS_DIR).
        </p>
      </section>
    </div>
  );
}
