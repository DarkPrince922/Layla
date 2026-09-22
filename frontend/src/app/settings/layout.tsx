"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { ArrowLeft, Menu, X } from "lucide-react";
import clsx from "clsx";
import { AuthGate } from "@/components/AuthGate";
import { SETTINGS_SECTIONS } from "@/lib/settings-nav";
import { useLocale } from "@/store/locale";
import { useAuth } from "@/store/auth";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const t = useLocale((s) => s.t);
  const isAdmin = useAuth((s) => s.user?.is_admin);
  const sections = SETTINGS_SECTIONS.filter((s) => !s.adminOnly || isAdmin);
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [pathname]);
  return (
    <AuthGate>
      <div className="flex h-dvh flex-col overflow-hidden md:flex-row">
        <div className="flex shrink-0 items-center gap-3 border-b border-ink-700 bg-ink-900 px-4 py-2 md:hidden">
          <button onClick={() => setMenu(!menu)} aria-label={menu ? "Закрыть меню настроек" : "Открыть меню настроек"} aria-expanded={menu}>
            {menu ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <Link href="/osint" className="text-sm">Назад в приложение</Link>
        </div>
        <aside className={`${menu ? "flex" : "hidden"} min-h-0 w-full flex-1 flex-col border-r border-ink-700 bg-ink-900 md:flex md:w-64 md:flex-none md:shrink-0`}>
          <Link
            href="/code"
            className="flex items-center gap-2 border-b border-ink-700 px-4 py-4 text-sm text-neutral-300 hover:text-white"
          >
            <ArrowLeft className="h-4 w-4" /> {t("back.toApp")}
          </Link>
          <nav className="flex-1 overflow-y-auto p-2">
            {sections.map((s) => {
              const active = pathname === `/settings/${s.slug}`;
              return (
                <Link
                  key={s.slug}
                  href={`/settings/${s.slug}`}
                  className={clsx(
                    "block rounded-md px-3 py-2 text-sm",
                    active ? "bg-ink-700 text-white" : "text-neutral-300 hover:bg-ink-800"
                  )}
                >
                  {t(`settings.${s.slug}`)}
                </Link>
              );
            })}
          </nav>
        </aside>
        <main className={`${menu ? "hidden md:block" : "block"} min-w-0 flex-1 overflow-y-auto`}>
          <div className="mx-auto max-w-3xl px-4 py-6 md:px-8 md:py-8">{children}</div>
        </main>
      </div>
    </AuthGate>
  );
}
