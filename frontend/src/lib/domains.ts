import { Code2, Crosshair, Search, Palette, type LucideIcon } from "lucide-react";

export interface DomainDef {
  slug: string;
  label: string;
  icon: LucideIcon;
  color: string;
  tagline: string;
  tabs: string[];
}

// slug остаётся латиницей (используется в маршрутах); переведены только
// отображаемые строки.
export const DOMAINS: DomainDef[] = [
  {
    slug: "code",
    label: "Код",
    icon: Code2,
    color: "#3b82f6",
    tagline: "Чат-агент с доступом к файлам проекта и репозиториям.",
    tabs: ["Чат", "Файлы", "Репозитории"],
  },
  {
    slug: "pentest",
    label: "Пентест",
    icon: Crosshair,
    color: "#ef4444",
    tagline: "Авторизованные engagement'ы: scope, находки, отчёты, агент.",
    tabs: ["Обзор", "Находки", "Отчёты", "Серверы", "Активность", "Агент"],
  },
  {
    slug: "osint",
    label: "OSINT",
    icon: Search,
    color: "#06b6d4",
    tagline: "Кейсы разведки по открытым источникам через intelligence-API (пассивно).",
    tabs: ["Кейсы", "Таймлайн", "Источники"],
  },
  {
    slug: "design",
    label: "Дизайн",
    icon: Palette,
    color: "#ec4899",
    tagline: "Генерация UI-артефактов (HTML/React/Vue) по брифу.",
    tabs: ["Бриф", "Превью", "Код", "Дизайн-система"],
  },
];

export const domainBySlug = (slug: string) => DOMAINS.find((d) => d.slug === slug);
