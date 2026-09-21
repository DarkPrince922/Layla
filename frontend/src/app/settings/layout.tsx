"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import clsx from "clsx";
import { AuthGate } from "@/components/AuthGate";
import { SETTINGS_SECTIONS } from "@/lib/settings-nav";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <AuthGate>
      <div className="flex h-screen overflow-hidden">
        <aside className="flex w-64 shrink-0 flex-col border-r border-ink-700 bg-ink-900">
          <Link
            href="/code"
            className="flex items-center gap-2 border-b border-ink-700 px-4 py-4 text-sm text-neutral-300 hover:text-white"
          >
            <ArrowLeft className="h-4 w-4" /> Back to app
          </Link>
          <nav className="flex-1 overflow-y-auto p-2">
            {SETTINGS_SECTIONS.map((s) => {
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
                  {s.label}
                </Link>
              );
            })}
          </nav>
        </aside>
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-8 py-8">{children}</div>
        </main>
      </div>
    </AuthGate>
  );
}
