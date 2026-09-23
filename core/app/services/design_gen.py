"""Генерация UI-артефактов по брифу (спец. §5.6).

Из брифа собирается промпт; модель возвращает самодостаточный код. Для превью в
изолированном iframe нужен один HTML-документ, поэтому даже для React/Vue просим
модель вернуть цельный index.html (React — через CDN + Babel standalone).
Результат нарезается на файлы для панели Code.
"""
from __future__ import annotations

import re

_STACK_HINT = {
    "html": "чистый HTML5 + CSS + немного ванильного JS, всё inline в одном index.html",
    "react": "React 18 через CDN (react, react-dom) и Babel standalone, всё в одном index.html "
    "(компоненты в <script type='text/babel'>)",
    "vue": "Vue 3 через CDN (global build), всё в одном index.html",
}

# Креативность: (температура, указание модели). None — температура из настроек модели.
CREATIVITY = {
    "safe": (0.35, "Сдержанно: проверенные паттерны, спокойная сетка, никаких экспериментов."),
    "balanced": (None, "Баланс: современный узнаваемый стиль и пара запоминающихся деталей."),
    "bold": (0.85, "Смело: выразительная типографика, необычная композиция, яркие акценты."),
    "wild": (1.0, "Эксперимент: нарушай привычные шаблоны, удиви неожиданной композицией и "
                  "деталями — но текст должен читаться, а интерфейс работать."),
}
# Макет — один большой ответ: стартовый лимит длины выше, чем у хода чата.
DESIGN_MAX_OUTPUT = 16384

_THEMES = {
    "light": "светлая",
    "dark": "тёмная",
    "both": "светлая и тёмная с переключателем (по умолчанию — как в системе)",
}
_PAGES = {
    "Single": "одна страница",
    "Multi": "несколько экранов в одном файле (навигация по якорям или вкладкам)",
}

# (ключ брифа, подпись в промпте) — в этом порядке поля попадают в задание.
_FIELDS = (
    ("brand", "Бренд / название"),
    ("industry", "Сфера"),
    ("audience", "Аудитория"),
    ("tone", "Тон"),
    ("layout", "Композиция"),
    ("palette", "Палитра"),
    ("accent_color", "Акцентный цвет"),
    ("fonts", "Шрифты"),
    ("density", "Плотность"),
    ("radius", "Скругления"),
    ("effects", "Эффекты"),
    ("sections", "Секции (по порядку)"),
    ("content", "Наполнение"),
    ("imagery", "Изображения"),
    ("icons", "Иконки"),
    ("animation", "Анимации"),
    ("interactivity", "Интерактивность"),
    ("device", "Приоритет устройств"),
    ("css", "CSS"),
    ("reference", "Референс"),
)


def _text(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip()


def temperature(brief: dict) -> float | None:
    return CREATIVITY.get(brief.get("creativity") or "balanced", CREATIVITY["balanced"])[0]


def build_prompt(brief: dict, stack: str) -> list[dict[str, str]]:
    """Собрать сообщения для LLM из брифа дизайна."""
    hint = _STACK_HINT.get(stack, _STACK_HINT["html"])
    theme = brief.get("theme") or "both"
    pages = brief.get("pages") or "Single"
    parts = [
        f"Тип артефакта: {_text(brief.get('artifact_type')) or 'Landing'}",
        f"Стилевое направление: {_text(brief.get('direction')) or 'Modern minimal'}",
        f"Тема: {_THEMES.get(theme, theme)}",
        f"Страницы: {_PAGES.get(pages, pages)}",
    ]
    for key, label in _FIELDS:
        value = _text(brief.get(key))
        if value:
            parts.append(f"{label}: {value}")
    language = _text(brief.get("language")) or "русский"
    parts.append(f"Язык всех текстов интерфейса: {language}")
    if brief.get("accessibility"):
        parts.append("Доступность: WCAG AA — контраст, семантика, aria-атрибуты, видимый фокус, "
                     "работа с клавиатуры, prefers-reduced-motion")
    creativity = CREATIVITY.get(brief.get("creativity") or "balanced", CREATIVITY["balanced"])[1]
    parts.append(f"Креативность: {creativity}")
    if brief.get("notes"):
        parts.append(f"Доп. требования: {_text(brief['notes'])}")

    system = (
        "Ты — ведущий дизайн-инженер. Сделай законченный, красивый и отзывчивый UI-артефакт "
        "уровня лучших продуктовых студий: продуманная типографическая шкала, сетка и ритм "
        "отступов, согласованная палитра через CSS-переменные, состояния hover/focus/active, "
        "реалистичные тексты по теме (не lorem ipsum), корректная вёрстка от 360px до широких экранов. "
        f"Стек: {hint}. Верни ЕДИНСТВЕННЫЙ полный документ index.html в одном блоке "
        "```html ... ```, без пояснений до и после. Никаких внешних сборок, только CDN. "
        "Пиши документ по порядку: сначала <head> со всеми стилями, затем разметку секций "
        "сверху вниз, скрипты — в конце <body>: так превью можно показывать по ходу генерации. "
        "Картинки — только встроенный SVG, CSS-графика или https://picsum.photos; шрифты — Google Fonts."
    )
    user = "Бриф дизайна:\n" + "\n".join(f"- {p}" for p in parts)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def design_caps(model_caps: dict | None, brief: dict) -> dict:
    """Настройки модели из «Провайдеры → модель» с поправками для генерации макета."""
    caps = dict(model_caps or {})
    if not caps.get("max_output_manual"):
        # «Авто»: макет длинный, начинаем не меньше DESIGN_MAX_OUTPUT, но не выше
        # известного предела модели. Если и так не влезет — _turn поднимет лимит сам.
        want = max(caps.get("max_output") or 0, DESIGN_MAX_OUTPUT)
        cap = caps.get("max_output_cap")
        caps["max_output"] = min(want, cap) if cap else want
    value = temperature(brief)
    if value is not None:
        caps["temperature"] = value  # явный выбор в брифе важнее общей настройки модели
    return caps


_FENCE = re.compile(r"```[ \t]*(?:html|htm)?[ \t]*\n?", re.IGNORECASE)


def extract_html(text: str) -> str:
    """Извлечь HTML из ответа модели (из ```html```-блока или как есть).

    Незакрытый блок (ответ оборвался на лимите длины) тоже годится: берём всё после
    открывающей ```html — браузер сам закроет недописанные теги.
    """
    m = re.search(r"```(?:html|htm)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    opening = _FENCE.search(text)
    if opening:
        return text[opening.end():].strip()
    return text.strip()


def to_files(html: str) -> list[dict]:
    """Представить артефакт как список файлов (для панели Code)."""
    return [{"name": "index.html", "content": html, "language": "html"}]
