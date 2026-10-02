"use client";

import { Sparkles, Type, Accessibility } from "lucide-react";
import { useAppearance } from "@/store/appearance";
import { AuroraBackdrop } from "@/components/AuroraBackdrop";

export function AppearanceSettings() {
  const { motion, setMotion } = useAppearance();
  return <div className="max-w-2xl">
    <h1>Внешний вид</h1>
    <p className="mb-7 text-sm text-neutral-400">Мягкий свет, глубокие оттенки и пространство для ваших идей.</p>
    <section className="mb-5">
      <div className="mb-4 flex flex-wrap items-center gap-3"><span className="chat-avatar"><Sparkles className="h-4 w-4" /></span><h2 className="font-semibold">Aurora</h2><span className="ml-auto rounded-full bg-accent-500/15 px-3 py-1 text-xs text-accent-200">Активна</span></div>
      <div className="appearance-preview"><AuroraBackdrop /><span className="mb-4 inline-flex rounded-full border border-accent-300/20 bg-accent-500/10 px-3 py-1 text-xs text-accent-200">Ваше рабочее пространство</span><p className="appearance-preview-heading">От идеи — к результату.</p><p className="mt-2 max-w-sm text-sm leading-relaxed text-neutral-400">Графит, лаванда и мятное свечение. Свет движется мягко, а главное остаётся в фокусе.</p><div className="mt-5 flex gap-2" aria-label="Палитра Aurora">{["bg-ink-950", "bg-ink-800", "bg-accent-600", "bg-accent-300", "bg-domain-osint"].map(c => <span key={c} className={`h-8 flex-1 rounded-lg border border-neutral-300/20 ${c}`} />)}</div></div>
      <p className="mt-4 text-sm leading-relaxed text-neutral-400">Единое оформление чатов, проектов, инструментов и настроек. У каждого раздела — свой цветовой акцент.</p>
    </section>
    <section className="mb-5 flex items-start gap-3"><Type className="mt-1 h-5 w-5 shrink-0 text-accent-300" /><div><h2 className="font-semibold">Manrope</h2><p className="mt-1 text-sm leading-relaxed text-neutral-400">Чёткий шрифт с поддержкой кириллицы. Для кода используется отдельный моноширинный шрифт.</p><p className="mt-5 text-xl">Привет, я Layla. Что создадим?</p></div></section>
    <section><label className="flex cursor-pointer items-start gap-3"><Accessibility className="mt-1 h-5 w-5 shrink-0 text-accent-300" /><span className="min-w-0 flex-1"><span className="block font-semibold">Плавные переходы и переливы фона</span><span className="mt-1 block text-sm leading-relaxed text-neutral-400">Мягкое появление экранов, панелей и сообщений, медленное движение света. При отключении фон становится статичным. Системное уменьшение движения всегда учитывается.</span></span><input type="checkbox" checked={motion} onChange={e => setMotion(e.target.checked)} className="mt-1 h-5 w-5" /></label></section>
  </div>;
}
