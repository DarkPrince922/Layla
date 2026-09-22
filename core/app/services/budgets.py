"""Учёт бюджетов агента (спец. §5.2 Ultracode).

Ограничения: токены на ход, стоимость (USD), время суб-агента (мин, 0=выкл).
При превышении оркестратор останавливает выполнение.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budgets:
    tokens_per_turn: int = 0      # 0 = без лимита
    cost_usd: float = 0.0         # 0 = без лимита
    subagent_minutes: float = 0.0  # 0 = выкл (без лимита времени)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Budgets":
        d = d or {}
        return cls(
            tokens_per_turn=int(d.get("tokens_per_turn", 0) or 0),
            cost_usd=float(d.get("cost_usd", 0) or 0),
            subagent_minutes=float(d.get("subagent_minutes", 0) or 0),
        )


@dataclass
class BudgetTracker:
    budgets: Budgets
    tokens: int = 0
    cost: float = 0.0
    used: dict = field(default_factory=dict)

    def add(self, *, tokens: int = 0, cost: float = 0.0) -> None:
        self.tokens += tokens
        self.cost += cost
        self.used = {"tokens": self.tokens, "cost": round(self.cost, 6)}

    def check(self, elapsed_seconds: float = 0.0) -> None:
        b = self.budgets
        if b.tokens_per_turn and self.tokens > b.tokens_per_turn:
            raise BudgetExceeded(f"Превышен лимит токенов ({self.tokens}/{b.tokens_per_turn})")
        if b.cost_usd and self.cost > b.cost_usd:
            raise BudgetExceeded(f"Превышен бюджет стоимости (${self.cost:.4f}/${b.cost_usd})")
        if b.subagent_minutes and elapsed_seconds > b.subagent_minutes * 60:
            raise BudgetExceeded(f"Превышен лимит времени ({b.subagent_minutes} мин)")
