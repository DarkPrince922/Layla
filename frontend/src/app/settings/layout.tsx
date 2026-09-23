"use client";

import { useAuth } from "@/store/auth";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ArrowLeft, Settings2, Palette, Cable, Bot, Users, BookOpen, ShieldCheck, Plug, Code2, Database, FlaskConical, Network, Trash2 } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { SETTINGS_SECTIONS } from "@/lib/settings-nav";
import { useLocale } from "@/store/locale";

const icons = [Settings2, Users, Palette, Cable, Bot, Users, BookOpen, ShieldCheck, Plug, Code2, Database, FlaskConical, Network, Trash2];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const user = useAuth(s => s.user);
  const sections = SETTINGS_SECTIONS.filter(s => !s.adminOnly || user?.is_admin);
  const pathname = usePathname();
  const router = useRouter();
  const t = useLocale((s) => s.t);
  return <AppShell>
    <div className="settings-layout flex h-full min-h-0 flex-col md:flex-row">
      <aside className="settings-sidebar m-3 mr-0 hidden w-60 shrink-0 flex-col rounded-2xl bg-ink-800/50 p-3 md:flex">
        <Link href="/code" className="mb-5 flex items-center gap-2 rounded-lg px-3 py-3 text-sm text-neutral-400 hover:bg-ink-700 hover:text-neutral-100"><ArrowLeft className="h-4 w-4" />{t("back.toApp")}</Link>
        <div className="mb-3 px-3 text-xs font-semibold text-neutral-500">{t("nav.settings")}</div>
        <nav aria-label="Разделы настроек" className="min-h-0 space-y-1 overflow-y-auto">
          {sections.map((s, index) => {
            const Icon = icons[SETTINGS_SECTIONS.indexOf(s)] || Settings2;
            const active = pathname === `/settings/${s.slug}`;
            return <Link key={s.slug} href={`/settings/${s.slug}`} aria-current={active ? "page" : undefined} className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm ${active ? "bg-accent-500/15 text-accent-200" : "text-neutral-400 hover:bg-ink-700/60 hover:text-neutral-100"}`}><Icon className="h-4 w-4 shrink-0" />{t(`settings.${s.slug}`)}</Link>;
          })}
        </nav>
      </aside>
      <div className="settings-mobile-nav shrink-0 px-5 pt-5 md:hidden">
        <label htmlFor="settings-section" className="mb-2 block text-xs text-neutral-500">{t("nav.settings")}</label>
        <select id="settings-section" value={pathname} onChange={e => router.push(e.target.value)} className="field-input">{sections.map(s => <option key={s.slug} value={`/settings/${s.slug}`}>{t(`settings.${s.slug}`)}</option>)}</select>
      </div>
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto"><div className="settings-content">{children}</div></div>
    </div>
  </AppShell>;
}
