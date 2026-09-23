"use client";

import { ActivityPanel } from "@/components/ActivityPanel";
import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { LogOut, Settings2, ChevronDown } from "lucide-react";
import { DOMAINS } from "@/lib/domains";
import { useAuth } from "@/store/auth";
import { useLocale } from "@/store/locale";
import { useLayout } from "@/store/layout";
import { useAppearance } from "@/store/appearance";
import { AuthGate } from "@/components/AuthGate";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const t = useLocale((s) => s.t);
  const applyLayout = useLayout((s) => s.apply);
  const applyAppearance = useAppearance((s) => s.apply);
  const accountMenu = useRef<HTMLDetailsElement>(null);
  const domain = DOMAINS.find((d) => pathname.startsWith(`/${d.slug}`));
  useEffect(() => { applyLayout(); applyAppearance(); }, [applyLayout, applyAppearance]);
  useEffect(() => { if (accountMenu.current) accountMenu.current.open = false; }, [pathname]);
  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (accountMenu.current && !accountMenu.current.contains(event.target as Node)) accountMenu.current.open = false;
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && accountMenu.current?.open) {
        accountMenu.current.open = false;
        accountMenu.current.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", escape); };
  }, []);

  return (
    <AuthGate>
      <div className="app-shell">
        <header className="app-topbar">
          <Link href="/code" className="flex shrink-0 items-center gap-2.5 rounded-xl" aria-label="Layla — главная">
            <span className="brand-mark">L</span><span className="text-xl font-semibold tracking-tight">Layla</span>
          </Link>
          <nav aria-label="Разделы Layla" className="hidden items-center gap-1 md:flex">
            {DOMAINS.map((d) => <Link key={d.slug} href={`/${d.slug}`} className="nav-pill" aria-current={domain?.slug === d.slug ? "page" : undefined}>
              <d.icon className="h-4 w-4" />{t(`domain.${d.slug}`)}
            </Link>)}
          </nav>
          <select aria-label="Раздел Layla" value={domain?.slug || "settings"} onChange={(e) => router.push(e.target.value === "settings" ? "/settings/general" : `/${e.target.value}`)} className="min-w-0 rounded-full border border-ink-600/70 bg-ink-800 px-3 py-2 text-sm md:hidden">
            {DOMAINS.map((d) => <option key={d.slug} value={d.slug}>{t(`domain.${d.slug}`)}</option>)}
            <option value="settings">{t("nav.settings")}</option>
          </select>
          <div className="ml-auto flex shrink-0 items-center gap-1 md:gap-3">
            <ActivityPanel /><Link href="/settings/general" className="icon-button hidden sm:inline-flex" aria-label={t("nav.settings")} aria-current={pathname.startsWith("/settings") ? "page" : undefined}><Settings2 className="h-[18px] w-[18px]" /></Link>
            <details ref={accountMenu} className="relative">
              <summary aria-label="Меню аккаунта" className="flex min-h-10 cursor-pointer list-none items-center gap-2 rounded-full p-1 text-neutral-400 [&::-webkit-details-marker]:hidden">
                <span className="grid h-8 w-8 place-items-center rounded-full border border-accent-400/20 bg-accent-500/15 text-sm font-semibold text-accent-200">{(user?.display_name || user?.email || "L").slice(0, 1).toUpperCase()}</span><ChevronDown className="hidden h-3.5 w-3.5 sm:block" />
              </summary>
              <div className="pane-enter absolute right-0 top-full z-50 mt-3 w-64 rounded-xl border border-ink-600 bg-ink-800 p-2 shadow-floating">
                <div className="truncate border-b border-ink-600/60 px-3 py-3 text-xs text-neutral-400">{user?.email}</div>
                <Link href="/settings/general" className="flex items-center gap-3 rounded-lg px-3 py-3 text-sm hover:bg-ink-700"><Settings2 className="h-4 w-4" />{t("nav.settings")}</Link>
                <button onClick={() => logout()} className="flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left text-sm text-neutral-400 hover:bg-ink-700"><LogOut className="h-4 w-4" />{t("nav.signout")}</button>
              </div>
            </details>
          </div>
        </header>
        <main className="app-main"><div key={pathname} className="route-view">{children}</div></main>
      </div>
    </AuthGate>
  );
}
