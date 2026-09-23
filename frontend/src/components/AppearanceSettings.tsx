"use client";

import { Sparkles, Type, Accessibility } from "lucide-react";
import { useAppearance } from "@/store/appearance";

export function AppearanceSettings() {
  const { motion, setMotion } = useAppearance();
  return <div className="max-w-2xl">
    <h1>Внешний вид</h1>
    <p className="mb-7 text-sm text-neutral-400">Спокойное пространство для ваших идей и проектов.</p>
    <section className="mb-5">
      <div className="mb-4 flex items-center gap-3"><span className="chat-avatar"><Sparkles className="h-4 w-4" /></span><h2 className="font-semibold">Мягкая тёмная тема</h2><span className="ml-auto rounded-full bg-accent-500/15 px-3 py-1 text-xs text-accent-200">Активна</span></div>
      <div className="flex gap-3" aria-label="Сливовая палитра Layla">{["bg-ink-950", "bg-ink-800", "bg-ink-600", "bg-accent-600", "bg-accent-300"].map(c => <span key={c} className={`h-12 flex-1 rounded-lg border border-neutral-300/10 ${c}`} />)}</div>
      <p className="mt-4 text-sm leading-relaxed text-neutral-400">Сливовые оттенки, округлые панели и лавандовый акцент во всех разделах.</p>
    </section>
    <section className="mb-5 flex items-start gap-3"><Type className="mt-1 h-5 w-5 shrink-0 text-accent-300" /><div><h2 className="font-semibold">Manrope</h2><p className="mt-1 text-sm leading-relaxed text-neutral-400">Чёткий шрифт с поддержкой кириллицы. Для кода используется отдельный моноширинный шрифт.</p><p className="mt-5 text-xl">Привет, я Layla. Что создадим?</p></div></section>
    <section><label className="flex cursor-pointer items-start gap-3"><Accessibility className="mt-1 h-5 w-5 shrink-0 text-accent-300" /><span className="min-w-0 flex-1"><span className="block font-semibold">Плавные переходы</span><span className="mt-1 block text-sm leading-relaxed text-neutral-400">Мягкое появление экранов и панелей. Системное уменьшение движения всегда учитывается.</span></span><input type="checkbox" checked={motion} onChange={e => setMotion(e.target.checked)} className="mt-1 h-5 w-5" /></label></section>
  </div>;
}
