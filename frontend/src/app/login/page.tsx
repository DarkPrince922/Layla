"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/store/auth";
import { HttpKeyBanner } from "@/components/HttpKeyBanner";

export default function LoginPage() {
  const router = useRouter();
  const { user, loaded, fetchMe, login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loaded) fetchMe();
  }, [loaded, fetchMe]);
  useEffect(() => {
    if (loaded && user) router.replace("/code");
  }, [loaded, user, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, name || undefined);
      router.replace("/code");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-ink-950 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <div className="grid h-8 w-8 place-items-center rounded-md bg-gradient-to-br from-fuchsia-500 to-indigo-500 font-bold text-white">
            L
          </div>
          <span className="text-2xl font-semibold">Layla</span>
        </div>

        <div className="mb-4">
          <HttpKeyBanner />
        </div>

        <form onSubmit={submit} className="space-y-3 rounded-lg border border-ink-700 bg-ink-900 p-5">
          <div className="flex gap-1 rounded-md bg-ink-800 p-1 text-sm">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`flex-1 rounded px-3 py-1.5 capitalize ${
                  mode === m ? "bg-ink-600 text-white" : "text-neutral-400"
                }`}
              >
                {m}
              </button>
            ))}
          </div>

          {mode === "register" && (
            <input
              className="w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-indigo-500"
              placeholder="Display name (optional)"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          )}
          <input
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-indigo-500"
            placeholder="Email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <input
            className="w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm outline-none focus:border-indigo-500"
            placeholder="Password"
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />

          {error && <p className="text-sm text-red-400">{error}</p>}

          <button
            disabled={busy}
            className="w-full rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <p className="mt-4 text-center text-xs text-neutral-600">
          Single-tenant self-hosted workstation. Your data stays on your server.
        </p>
      </div>
    </div>
  );
}
