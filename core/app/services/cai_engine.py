"""Обёртка над CAI как опциональным движком исполнения (спец. §2, §5.4, M5).

CAI (cai-framework) интегрируется как ДВИЖОК за гейтами Layla: любой запуск
команд возможен только через venue_executor.execute (scope/venue/egress + HITL).
Сам по себе этот модуль ничего не атакует: он лишь сообщает о доступности CAI и
предоставляет фабрику runner'а, которую оператор подключает осознанно на
авторизованном engagement'е. Импорт ленивый и опциональный.
"""
from __future__ import annotations


def cai_available() -> bool:
    try:
        import cai  # noqa: F401
        return True
    except Exception:
        return False


def status() -> dict:
    available = cai_available()
    return {
        "engine": "cai",
        "available": available,
        "note": (
            "CAI установлен. Исполнение возможно только через гейты Layla "
            "(scope/venue/egress + HITL) на авторизованном engagement'е."
            if available
            else "CAI не установлен (pip install cai-framework). Агент работает в "
            "режиме планирования и HITL; активные команды требуют настроенного "
            "исполнителя и проходят гейты безопасности."
        ),
    }
