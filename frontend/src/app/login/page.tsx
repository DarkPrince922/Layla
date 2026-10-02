"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/store/auth";
import { HttpKeyBanner } from "@/components/HttpKeyBanner";
import { useLocale } from "@/store/locale";
import { api, type Bootstrap } from "@/lib/api";
import { AuroraBackdrop } from "@/components/AuroraBackdrop";
import { DOMAINS } from "@/lib/domains";

export default function LoginPage() {
  const router = useRouter();
  const { user, loaded, fetchMe, login } = useAuth();
  const t = useLocale((st) => st.t);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [boot, setBoot] = useState<Bootstrap | null>(null);

  useEffect(() => {
    if (!loaded) fetchMe();
  }, [loaded, fetchMe]);
  useEffect(() => {
    if (loaded && user) router.replace("/code");
  }, [loaded, user, router]);
  // Первый запуск: показать одноразовые учётные данные администратора.
  useEffect(() => {
    api
      .get<Bootstrap>("/auth/bootstrap")
      .then((b) => {
        if (b.available) {
          setBoot(b);
          setEmail((e) => e || b.email || "");
        }
      })
      .catch(() => {});
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
      router.replace("/code");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Что-то пошло не так");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-shell">
      <AuroraBackdrop />
      <div className="login-layout">
      <div className="login-intro"><div className="mb-10 flex items-center gap-3"><span className="brand-mark">L</span><span className="text-2xl font-semibold tracking-tight">Layla</span></div><h2>Ваши идеи.<br /><span className="text-accent-200">Новые возможности.</span></h2><p className="mt-6 max-w-md text-base leading-relaxed text-neutral-400">Одно пространство для кода, исследований и дизайна. Дайте задачу — Layla поможет воплотить её.</p><div className="mt-8 flex flex-wrap gap-2">{DOMAINS.map(d => <span key={d.slug} className="inline-flex items-center gap-2 rounded-full border border-ink-600/60 bg-ink-800/40 px-3 py-2 text-xs text-neutral-300"><d.icon className="h-3.5 w-3.5 text-accent-300" />{t(`domain.${d.slug}`)}</span>)}</div></div>
      <div className="login-card">
        <div className="mb-7 flex items-center justify-center gap-3 min-[900px]:hidden"><span className="brand-mark">L</span><span className="text-2xl font-semibold tracking-tight">Layla</span></div>

        <div className="mb-4">
          <HttpKeyBanner />
        </div>

        {boot && (
          <div className="mb-4 rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4 text-xs">
            <p className="mb-2 font-semibold text-amber-300">
              Первый запуск — учётная запись администратора создана
            </p>
            <p className="mb-2 leading-relaxed text-amber-100/80">
              Сохраните пароль: после первого входа он больше не будет показан.
            </p>
            <div className="space-y-1 font-mono text-[13px]">
              <div>
                <span className="text-neutral-400">Логин: </span>
                <span className="select-all text-white">{boot.email}</span>
              </div>
              <div>
                <span className="text-neutral-400">Пароль: </span>
                <span className="select-all text-white">{boot.password}</span>
              </div>
            </div>
          </div>
        )}

        <form onSubmit={submit} className="login-form space-y-3 rounded-2xl border border-ink-700 bg-ink-900/90 p-6 md:p-8">
          <h1 className="text-sm font-semibold text-neutral-200">{t("login.signin")}</h1>
          <p className="text-sm text-neutral-400">Рады видеть вас в Layla.</p>
          <input
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-accent-500"
            placeholder={t("login.email")}
            aria-label={t("login.email")}
            autoComplete="username"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <input
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-accent-500"
            placeholder={t("login.password")}
            aria-label={t("login.password")}
            autoComplete="current-password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />

          {error && (
            <p role="alert" className="text-sm text-red-400">
              {error}
            </p>
          )}

          <button
            disabled={busy}
            className="primary-button w-full"
          >
            {busy ? "…" : t("login.signin")}
          </button>
        </form>
        <p className="mt-4 text-center text-xs text-neutral-600">
          Новые учётные записи создаёт администратор в разделе «Пользователи».
        </p>
      </div>
      </div>
    </div>
  );
}
