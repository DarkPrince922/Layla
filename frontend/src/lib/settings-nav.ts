// Разделы настроек (спец. §5 / §3). Каждый — маршрут под /settings.
export interface SettingsSection {
  slug: string;
  label: string;
  blurb: string;
  adminOnly?: boolean;
}

export const SETTINGS_SECTIONS: SettingsSection[] = [
  { slug: "general", label: "Общие", blurb: "Горячие клавиши, папка проектов, раскладка." },
  { slug: "users", label: "Пользователи", blurb: "Учётные записи, роли, сброс паролей.", adminOnly: true },
  { slug: "appearance", label: "Внешний вид", blurb: "Темы и акценты." },
  { slug: "providers", label: "Провайдеры", blurb: "Профили подключения LLM (через LiteLLM)." },
  { slug: "agent", label: "Агент", blurb: "Оркестрация Ultracode, бюджеты, диагностика." },
  { slug: "personas", label: "Персоны", blurb: "Роли ИИ, доступ к инструментам и рамки." },
  { slug: "knowledge", label: "База знаний", blurb: "Документы RAG, индексация в pgvector." },
  { slug: "security", label: "Безопасность", blurb: "Политики доступа, подтверждения, аудит-лог." },
  { slug: "integrations", label: "Интеграции", blurb: "MCP-серверы, Telegram, intelligence-API." },
  { slug: "local-api", label: "Локальный API", blurb: "Собственный OpenAI-совместимый эндпоинт Layla." },
  { slug: "data", label: "Данные", blurb: "Экспорт, импорт, ретеншн, очистка." },
  { slug: "accounts-lab", label: "Accounts Lab", blurb: "Подключение провайдеров, ротация, combos." },
  { slug: "privacy-chain", label: "Privacy Chain", blurb: "Глобальный egress-профиль (Direct/Tor/Proxy)." },
];
