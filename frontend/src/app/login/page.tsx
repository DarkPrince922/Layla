"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/store/auth";
import { HttpKeyBanner } from "@/components/HttpKeyBanner";
import { useLocale } from "@/store/locale";
import { api, type Bootstrap } from "@/lib/api";

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
    <div className="grid min-h-screen place-items-center bg-ink-950 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <div className="grid h-8 w-8 place-items-center rounded-md bg-gradient-to-br from-fuchsia-500 to-accent-500 font-bold text-white">
            L
          </div>
          <span className="text-2xl font-semibold">Layla</span>
        </div>

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

        <form onSubmit={submit} className="space-y-3 rounded-2xl border border-ink-700 bg-ink-900 p-5">
          <h1 className="text-sm font-semibold text-neutral-200">{t("login.signin")}</h1>
          <input
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-accent-500"
            placeholder={t("login.email")}
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <input
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-accent-500"
            placeholder={t("login.password")}
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
            className="w-full rounded-md bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-500 disabled:opacity-50"
          >
            {busy ? "…" : t("login.signin")}
          </button>
        </form>
        <p className="mt-4 text-center text-xs text-neutral-600">
          Новые учётные записи создаёт администратор в разделе «Пользователи».
        </p>
      </div>
    </div>
  );
}
