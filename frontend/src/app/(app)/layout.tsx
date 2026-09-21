"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { Sidebar } from "@/components/Sidebar";
import { AuthGate } from "@/components/AuthGate";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [menu, setMenu] = useState(false);
  const pathname = usePathname();
  useEffect(() => setMenu(false), [pathname]);
  return (
    <AuthGate>
      <div className="flex h-dvh flex-col overflow-hidden">
        <div className="flex shrink-0 items-center gap-3 border-b border-ink-700 bg-ink-900 px-4 py-2 md:hidden">
          <button onClick={() => setMenu(!menu)} aria-label={menu ? "Закрыть меню" : "Открыть меню"} aria-expanded={menu}>
            {menu ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <span className="text-sm font-semibold">Layla</span>
        </div>
        <div className="flex min-h-0 flex-1">
          <div className={`${menu ? "block w-full" : "hidden"} md:block md:w-auto`}><Sidebar /></div>
          <main className={`${menu ? "hidden md:block" : "block"} min-w-0 flex-1 overflow-y-auto`}>{children}</main>
        </div>
      </div>
    </AuthGate>
  );
}
