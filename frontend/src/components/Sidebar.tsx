"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Settings, Search, FolderTree, MessageSquare, Activity } from "lucide-react";
import clsx from "clsx";
import { DOMAINS } from "@/lib/domains";
import { useAuth } from "@/store/auth";
import { useLocale } from "@/store/locale";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4">
      <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
        {title}
      </div>
      {children}
    </div>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const user = useAuth((s) => s.user);
  const t = useLocale((s) => s.t);
  const logout = useAuth((s) => s.logout);

  return (
    <aside className="flex h-full w-full shrink-0 flex-col border-r border-ink-700 bg-ink-900 md:w-64">
      <div className="flex items-center gap-2 px-4 py-4">
        <div className="grid h-7 w-7 place-items-center rounded-md bg-gradient-to-br from-fuchsia-500 to-indigo-500 text-sm font-bold text-white">
          L
        </div>
        <span className="text-lg font-semibold tracking-tight">Layla</span>
      </div>

      <button className="mx-3 mb-1 flex items-center gap-2 rounded-md border border-ink-700 bg-ink-800 px-3 py-1.5 text-xs text-neutral-400 hover:text-neutral-200">
        <Search className="h-3.5 w-3.5" /> {t("nav.search")}
        <span className="ml-auto rounded bg-ink-600 px-1.5 py-0.5 text-[10px]">⌘K</span>
      </button>

      <nav className="flex-1 overflow-y-auto px-2 pb-4">
        <Section title={t("nav.domains")}>
          {DOMAINS.map((d) => {
            const active = pathname === `/${d.slug}` || pathname.startsWith(`/${d.slug}/`);
            const Icon = d.icon;
            return (
              <Link
                key={d.slug}
                href={`/${d.slug}`}
                className={clsx(
                  "group flex items-center gap-2.5 rounded-md px-3 py-2 text-sm",
                  active ? "bg-ink-700 text-white" : "text-neutral-300 hover:bg-ink-800"
                )}
              >
                <Icon className="h-4 w-4" style={{ color: d.color }} />
                {t(`domain.${d.slug}`)}
                <span
                  className="ml-auto h-1.5 w-1.5 rounded-full opacity-0 group-hover:opacity-60"
                  style={{ backgroundColor: d.color }}
                />
              </Link>
            );
          })}
        </Section>

        <Section title={t("nav.working")}>
          <div className="px-3 py-2 text-xs text-neutral-500 flex items-center gap-2">
            <Activity className="h-3.5 w-3.5" /> {t("nav.noTasks")}
          </div>
        </Section>

        <Section title={t("nav.workspace")}>
          <div className="px-3 py-2 text-xs text-neutral-500 flex items-center gap-2">
            <FolderTree className="h-3.5 w-3.5" /> {t("nav.noProjects")}
          </div>
        </Section>

        <Section title={t("nav.chats")}>
          <div className="px-3 py-2 text-xs text-neutral-500 flex items-center gap-2">
            <MessageSquare className="h-3.5 w-3.5" /> {t("nav.noChats")}
          </div>
        </Section>
      </nav>

      <div className="border-t border-ink-700 p-2">
        <Link
          href="/settings/general"
          className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-neutral-300 hover:bg-ink-800"
        >
          <Settings className="h-4 w-4" /> {t("nav.settings")}
        </Link>
        <div className="mt-1 flex items-center gap-2 px-3 py-2 text-xs text-neutral-500">
          <div className="grid h-6 w-6 place-items-center rounded-full bg-ink-600 text-[10px] text-neutral-300">
            {(user?.email || "?").slice(0, 1).toUpperCase()}
          </div>
          <span className="truncate">{user?.email || t("nav.notSignedIn")}</span>
          {user && (
            <button onClick={() => logout()} className="ml-auto text-neutral-500 hover:text-neutral-300">
              {t("nav.signout")}
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
