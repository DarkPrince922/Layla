"""Генерация UI-артефактов по брифу (спец. §5.6).

Из брифа собирается промпт; модель через LiteLLM возвращает самодостаточный
код. Для превью в изолированном iframe нужен один HTML-документ, поэтому даже
для React/Vue просим модель вернуть цельный index.html (React — через CDN +
Babel standalone). Результат нарезается на файлы для панели Code.
"""
from __future__ import annotations

import re

_STACK_HINT = {
    "html": "чистый HTML5 + CSS + немного ванильного JS, всё inline в одном index.html",
    "react": "React 18 через CDN (react, react-dom) и Babel standalone, всё в одном index.html "
    "(компоненты в <script type='text/babel'>)",
    "vue": "Vue 3 через CDN (global build), всё в одном index.html",
}


def build_prompt(brief: dict, stack: str) -> list[dict[str, str]]:
    """Собрать сообщения для LLM из брифа дизайна."""
    hint = _STACK_HINT.get(stack, _STACK_HINT["html"])
    parts = [
        f"Тип артефакта: {brief.get('artifact_type', 'Landing')}",
        f"Направление: {brief.get('direction', 'Modern minimal')}",
        f"Тон: {brief.get('tone', 'нейтральный')}",
        f"Тема: {brief.get('theme', 'both')}",
        f"Страницы: {brief.get('pages', 'Single')}",
    ]
    if brief.get("reference"):
        parts.append(f"Референс: {brief['reference']}")
    if brief.get("brand"):
        parts.append(f"Бренд: {brief['brand']}")
    if brief.get("notes"):
        parts.append(f"Доп. требования: {brief['notes']}")

    system = (
        "Ты — дизайн-инженер. Сгенерируй красивый, отзывчивый UI-артефакт. "
        f"Стек: {hint}. Верни ЕДИНСТВЕННЫЙ полный документ index.html в одном блоке "
        "```html ... ```, без пояснений. Никаких внешних сборок, только CDN."
    )
    user = "Бриф дизайна:\n" + "\n".join(parts)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def extract_html(text: str) -> str:
    """Извлечь HTML из ответа модели (из ```html```-блока или как есть)."""
    m = re.search(r"```(?:html)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    code = m.group(1).strip() if m else text.strip()
    return code


def to_files(html: str) -> list[dict]:
    """Представить артефакт как список файлов (для панели Code)."""
    return [{"name": "index.html", "content": html, "language": "html"}]
