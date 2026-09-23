/** Бриф «Дизайна»: варианты настроек, значения по умолчанию, «Удиви меня». */

export type Creativity = "safe" | "balanced" | "bold" | "wild";

export interface Brief {
  artifact_type: string;
  direction: string;
  tone: string;
  theme: string;
  pages: string;
  reference: string;
  brand: string;
  notes: string;
  industry: string;
  audience: string;
  language: string;
  layout: string;
  palette: string;
  accent_color: string;
  fonts: string;
  density: string;
  radius: string;
  effects: string[];
  sections: string[];
  content: string;
  imagery: string;
  icons: string;
  animation: string;
  interactivity: string;
  device: string;
  css: string;
  accessibility: boolean;
  creativity: Creativity;
}

/** Пустая строка — «Авто»: решает модель. */
export const DEFAULT_BRIEF: Brief = {
  artifact_type: "Лендинг",
  direction: "Современный минимализм",
  tone: "",
  theme: "both",
  pages: "Single",
  reference: "",
  brand: "",
  notes: "",
  industry: "",
  audience: "",
  language: "Русский",
  layout: "",
  palette: "",
  accent_color: "",
  fonts: "",
  density: "",
  radius: "",
  effects: [],
  sections: [],
  content: "",
  imagery: "",
  icons: "",
  animation: "",
  interactivity: "",
  device: "",
  css: "",
  accessibility: false,
  creativity: "balanced",
};

export const STACKS = [["html", "Plain HTML"], ["react", "React"], ["vue", "Vue"]] as const;

export const ARTIFACTS = [
  "Лендинг", "Дашборд", "Страница тарифов", "Мобильное приложение", "Интернет-магазин", "Карточка товара",
  "Портфолио", "Блог / статья", "Документация", "Админ-панель", "Вход и регистрация", "Онбординг",
  "Email-рассылка", "Презентация", "Резюме / CV", "Меню ресторана", "Страница события", "Coming soon / 404",
];

export const DIRECTIONS = [
  "Современный минимализм", "Editorial / журнальный", "Swiss / International", "Брутализм", "Glassmorphism",
  "Neumorphism", "Bento-сетка", "Тёплый и мягкий", "Tech utility", "Корпоративный", "Люкс / премиум",
  "Ретро / Y2K", "Киберпанк / неон", "Органика / природа", "Игривый / мультяшный", "Ар-деко", "Мемфис",
  "Монохром", "Рукописный / скетч", "В духе Apple",
];

export const TONES = [
  "Дружелюбный", "Деловой", "Премиальный", "Игривый", "Технологичный", "Тёплый", "Дерзкий", "Спокойный",
  "Вдохновляющий", "Экспертный",
];

export const THEMES = [["light", "Светлая"], ["dark", "Тёмная"], ["both", "Обе"]] as const;
export const PAGES = [["Single", "Одна страница"], ["Multi", "Несколько экранов"]] as const;

export const LANGUAGES = [
  "Русский", "English", "Українська", "Қазақша", "Deutsch", "Español", "Français", "Italiano", "Português",
  "Türkçe", "中文", "日本語", "العربية",
];

export const LAYOUTS = [
  "Классическая: секции по центру", "Асимметричная", "Bento-сетка", "Split-screen", "Журнальные колонки",
  "Карточная сетка", "Боковая навигация", "Полноэкранные секции", "Длинный сторителлинг",
];

export interface Palette { name: string; colors: string[] }

export const PALETTES: Palette[] = [
  { name: "Монохром", colors: ["#0A0A0A", "#2A2A2A", "#A3A3A3", "#FAFAFA"] },
  { name: "Океан", colors: ["#0B1D3A", "#1CA7EC", "#7FDBFF", "#E6F4FF"] },
  { name: "Закат", colors: ["#2B1B3D", "#FF6B6B", "#FFB86B", "#FFE9D6"] },
  { name: "Лес", colors: ["#1B2F23", "#3E7C59", "#A7C957", "#F2E8CF"] },
  { name: "Неон", colors: ["#0D0221", "#FF2A6D", "#05D9E8", "#D1F7FF"] },
  { name: "Пастель", colors: ["#FDF6F0", "#FFD6E0", "#C1E7E3", "#B8B8FF"] },
  { name: "Земляные", colors: ["#3D2B1F", "#A0522D", "#D2B48C", "#F5EBDC"] },
  { name: "Королевский", colors: ["#1A1446", "#5B3CC4", "#F2C14E", "#F7F4EA"] },
  { name: "Кофе", colors: ["#2C1E16", "#6F4E37", "#C8A27A", "#F3E9DC"] },
  { name: "Мята", colors: ["#0B3C49", "#2EC4B6", "#CBF3F0", "#F4FFF9"] },
  { name: "Красный акцент", colors: ["#111111", "#E63946", "#A8DADC", "#F1FAEE"] },
  { name: "Корпоративный синий", colors: ["#0F172A", "#1E40AF", "#3B82F6", "#F5F7FB"] },
];

export const paletteValue = (p: Palette) => `${p.name} (${p.colors.join(", ")})`;

export const FONTS = [
  "Гротеск: Inter / Manrope", "Геометрия: Space Grotesk", "Антиква: Playfair Display + Inter",
  "Редакционный: Fraunces + Source Sans 3", "Моноширинный: JetBrains Mono", "Округлый: Nunito",
  "Узкий display: Bebas Neue + Roboto", "Элегантный: Cormorant Garamond + Montserrat",
  "Рукописный акцент: Caveat + Inter", "Системный: system-ui",
];

export const DENSITY = ["Воздушно", "Сбалансировано", "Плотно"];
export const RADIUS = ["Острые углы", "Слегка скруглённые", "Скруглённые", "Пилюли"];

export const EFFECTS = [
  "Градиенты", "Стекло / blur", "Мягкие тени", "Зерно / шум", "3D и перспектива", "Паттерны на фоне",
  "Контурные рамки", "Свечение", "Параллакс", "Крупная типографика", "Бегущая строка", "Дудлы и стикеры",
];

export const SECTIONS = [
  "Первый экран (hero)", "Логотипы клиентов", "Возможности", "Как это работает", "Цифры и статистика",
  "Кейсы / портфолио", "Тарифы", "Отзывы", "Команда", "FAQ", "Блог", "Галерея", "Призыв к действию",
  "Форма контакта", "Футер",
];

export const CONTENT = ["Минимум текста", "Реалистичные тексты", "Подробно, много контента"];

export const IMAGERY = [
  "Без картинок — типографика и формы", "SVG-иллюстрации", "Фото-заглушки (picsum)",
  "Абстрактные формы и градиенты", "Мокапы интерфейса", "Эмодзи",
];

export const ICONS = ["Без иконок", "Lucide (CDN)", "Встроенные SVG", "Font Awesome (CDN)", "Эмодзи"];

export const ANIMATION = [
  "Без анимаций", "Лёгкие: hover и плавные переходы",
  "Выразительные: появление при прокрутке и микровзаимодействия",
];

export const INTERACTIVITY = [
  "Статика", "Табы, аккордеоны, модальные окна", "Рабочие формы с валидацией",
  "Демо-данные и графики (Chart.js)", "Корзина / фильтры на JS",
];

export const DEVICES = ["Все устройства", "Сначала мобильные", "Сначала десктоп"];
export const CSS_MODES = [["", "Авто"], ["Свой CSS", "Свой CSS"], ["Tailwind (CDN)", "Tailwind"]] as const;

export const CREATIVITY: { id: Creativity; label: string; hint: string }[] = [
  { id: "safe", label: "Сдержанно", hint: "Проверенные паттерны, спокойная сетка. Температура 0.35." },
  { id: "balanced", label: "Баланс", hint: "Современно и узнаваемо. Температура — из настроек модели." },
  { id: "bold", label: "Смело", hint: "Выразительная типографика и композиция. Температура 0.85." },
  { id: "wild", label: "Дерзко", hint: "Эксперимент: ломает шаблоны и удивляет. Температура 1.0." },
];

const STORAGE_KEY = "layla.design.brief.v2";

export function loadBrief(): Brief {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (saved && typeof saved === "object") {
      const brief = { ...DEFAULT_BRIEF, ...saved } as Brief;
      for (const key of ["effects", "sections"] as const) {
        if (!Array.isArray(brief[key])) brief[key] = [];
      }
      if (!CREATIVITY.some(c => c.id === brief.creativity)) brief.creativity = "balanced";
      return brief;
    }
  } catch { /* приватное окно или повреждённые данные — берём значения по умолчанию */ }
  return DEFAULT_BRIEF;
}

export function saveBrief(brief: Brief) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(brief)); } catch { /* не критично */ }
}

/** Для запроса: пустые поля («Авто») не отправляем. */
export function briefPayload(brief: Brief): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(brief)) {
    if (Array.isArray(value) ? value.length : value !== "") out[key] = typeof value === "string" ? value.trim() : value;
  }
  return out;
}

/** Сколько настроек в группе отличается от «Авто» — для подписи свёрнутой группы. */
export function configured(brief: Brief, keys: (keyof Brief)[]): number {
  return keys.filter(k => {
    const value = brief[k];
    return Array.isArray(value) ? value.length > 0 : value !== DEFAULT_BRIEF[k];
  }).length;
}

const pick = <T,>(list: readonly T[]) => list[Math.floor(Math.random() * list.length)];
function some<T>(list: readonly T[], min: number, max: number): T[] {
  const count = min + Math.floor(Math.random() * (max - min + 1));
  return [...list].sort(() => Math.random() - 0.5).slice(0, count);
}

/** «Удиви меня»: случайный стиль. Что делаем и для кого (тип, бренд, сфера, секции, язык) — не трогаем. */
export function surprise(brief: Brief): Brief {
  const accent = Math.random() < 0.5
    ? "#" + Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, "0").toUpperCase()
    : "";
  return {
    ...brief,
    direction: pick(DIRECTIONS),
    tone: some(TONES, 1, 2).join(", "),
    theme: pick(THEMES)[0],
    layout: pick(LAYOUTS),
    palette: accent ? "" : paletteValue(pick(PALETTES)),
    accent_color: accent,
    fonts: pick(FONTS),
    density: pick(DENSITY),
    radius: pick(RADIUS),
    effects: some(EFFECTS, 1, 3),
    imagery: pick(IMAGERY),
    animation: pick(ANIMATION.slice(1)),
    creativity: pick(["balanced", "bold", "bold", "wild"] as Creativity[]),
  };
}
