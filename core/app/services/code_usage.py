"""Code-only shared admission budget; conservative reservations survive crashes.

USD values are estimates at user-supplied prices, never provider invoices.
A missing usage report or an uncertain failure retains the whole reservation.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.models.chat import Chat
from app.models.code_control import CodeUsageCall, CodeUsagePolicy

ACTIVE = ContextVar("code_usage", default=None)


class BudgetExceeded(RuntimeError):
    pass


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_tokens: int | None = Field(default=None, ge=1, le=10**12)
    max_usd: Decimal | None = Field(default=None, gt=0, le=10**6, decimal_places=6)
    input_usd_per_million: Decimal | None = Field(default=None, ge=0, le=10**5, decimal_places=6)
    output_usd_per_million: Decimal | None = Field(default=None, ge=0, le=10**5, decimal_places=6)
    web_search_usd_per_thousand: Decimal = Field(default=Decimal(10), ge=0, le=10**5, decimal_places=6)


def cost(prices, inputs, outputs):
    # Price USD / million tokens is numerically microUSD / token.
    return int((Decimal(prices.get("input", "0")) * inputs
                + Decimal(prices.get("output", "0")) * outputs
                + Decimal(prices.get("search_unit_microusd", "0")) * prices.get("searches", 0)).to_integral_value(rounding=ROUND_CEILING))


async def ensure(runtime):
    async with runtime.maker() as session:
        await runtime._authorize(session)
        if await session.get(CodeUsagePolicy, runtime.chat_id):
            return
        await session.scalar(select(Chat).where(Chat.id == runtime.chat_id).with_for_update())
        if await session.get(CodeUsagePolicy, runtime.chat_id):
            return
        session.add(CodeUsagePolicy(chat_id=runtime.chat_id))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()


async def configure(runtime, args, provider_id, model):
    await ensure(runtime)
    for _ in range(10):
        async with runtime.maker() as session:
            await runtime._authorize(session)
            row = await session.get(CodeUsagePolicy, runtime.chat_id)
            prices = dict(row.prices or {})
            if (args.input_usd_per_million is None) != (args.output_usd_per_million is None):
                raise ValueError("Укажите обе цены или оставьте обе пустыми")
            if args.input_usd_per_million is not None:
                if not provider_id or not model:
                    raise ValueError("Выберите провайдера и модель чата")
                prices[f"{provider_id}|{model}"] = {"input": str(args.input_usd_per_million),
                    "output": str(args.output_usd_per_million), "web_search": str(args.web_search_usd_per_thousand)}
            elif provider_id and model and f"{provider_id}|{model}" in prices:
                prices[f"{provider_id}|{model}"] = {**prices[f"{provider_id}|{model}"],
                    "web_search": str(args.web_search_usd_per_thousand)}
            elif args.web_search_usd_per_thousand != Decimal(10):
                raise ValueError("Для своей цены WebSearch задайте обе цены токенов выбранной модели")
            changed = await session.execute(update(CodeUsagePolicy).where(
                CodeUsagePolicy.chat_id == runtime.chat_id, CodeUsagePolicy.revision == row.revision
            ).values(max_tokens=args.max_tokens,
                max_cost_microusd=int(args.max_usd * 10**6) if args.max_usd is not None else None,
                prices=prices, revision=row.revision + 1))
            if changed.rowcount:
                await session.commit()
                return
    raise ValueError("Бюджет обновлён параллельно; повторите сохранение")


async def snapshot(runtime, provider_id=None, model=None):
    async with runtime.maker() as session:
        await runtime._authorize(session)
        policy = await session.get(CodeUsagePolicy, runtime.chat_id)
        calls = (await session.execute(select(CodeUsageCall.status, func.count()).where(
            CodeUsageCall.chat_id == runtime.chat_id).group_by(CodeUsageCall.status))).all()
        prices = (policy.prices or {}).get(f"{provider_id}|{model}", {}) if policy else {}
        return {"max_tokens": policy.max_tokens if policy else None,
                "max_usd": str(Decimal(policy.max_cost_microusd) / 10**6) if policy and policy.max_cost_microusd is not None else None,
                "tokens_used": policy.tokens_used if policy else 0,
                "estimated_usd": str(Decimal(policy.cost_used) / 10**6) if policy else "0",
                "prices": prices, "calls": dict(calls), "model": model, "provider_id": provider_id,
                "note": "USD — оценка по заданным ценам; WebSearch по умолчанию 10 USD за 1000 поисков плюс токены. Без usage сохраняется полный резерв. Цена токенов отсутствующих моделей не включена в стоимость."}


class Account:
    def __init__(self, runtime, job_id=None):
        self.runtime, self.job_id = runtime, job_id

    async def reserve(self, provider_id, model, messages, tools, caps, *, server_searches=0):
        await ensure(self.runtime)
        # UTF-8 bytes plus protocol overhead deliberately overestimate common
        # tokenizers. Actual reported usage reconciles the reservation later.
        inputs = len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False).encode()) + 128 * (len(messages) + 1)
        if server_searches:
            inputs = max(inputs, caps.get("context") or 200000)
        outputs = caps.get("max_output") or 8192
        outputs = max(outputs, caps.get("reasoning_budget") or 0)
        for _ in range(12):
            async with self.runtime.maker() as session:
                await self.runtime._authorize(session)
                row = await session.get(CodeUsagePolicy, self.runtime.chat_id)
                prices = dict((row.prices or {}).get(f"{provider_id}|{model}", {}))
                has_prices = bool(prices)
                if server_searches:
                    prices.update(searches=server_searches, reserved_searches=server_searches,
                                  search_unit_microusd=str(Decimal(prices.get("web_search", "10")) * 1000))
                charge = cost(prices, inputs, outputs)
                if row.max_cost_microusd is not None and not has_prices:
                    raise BudgetExceeded("Для бюджета USD задайте цены выбранной модели в панели Code")
                if row.max_tokens is not None and row.tokens_used + inputs + outputs > row.max_tokens:
                    raise BudgetExceeded("Бюджет токенов Code исчерпан: следующий запрос не отправлен. Увеличьте лимит или сожмите контекст.")
                if row.max_cost_microusd is not None and row.cost_used + charge > row.max_cost_microusd:
                    raise BudgetExceeded("Бюджет стоимости Code исчерпан: следующий запрос не отправлен. Измените лимит в панели Code.")
                call = CodeUsageCall(chat_id=self.runtime.chat_id, project_id=self.runtime.project_id,
                    job_id=self.job_id, provider_id=str(provider_id), model=model, input_tokens=inputs,
                    output_tokens=outputs, charged_tokens=inputs + outputs, charged_cost=charge, prices=prices)
                changed = await session.execute(update(CodeUsagePolicy).where(
                    CodeUsagePolicy.chat_id == self.runtime.chat_id, CodeUsagePolicy.revision == row.revision
                ).values(tokens_used=row.tokens_used + inputs + outputs,
                         cost_used=row.cost_used + charge, revision=row.revision + 1))
                if changed.rowcount:
                    session.add(call)
                    await session.commit()
                    return call.id
        raise BudgetExceeded("Не удалось зарезервировать бюджет; повторите запрос")

    async def settle(self, call_id, usage=None, *, failed=False, rejected=False):
        for _ in range(12):
            async with self.runtime.maker() as session:
                call = await session.get(CodeUsageCall, call_id)
                if call is None or call.status != "reserved":
                    return
                row = await session.get(CodeUsagePolicy, self.runtime.chat_id)
                reported = (isinstance(usage, dict) and all(isinstance(usage.get(k), int)
                            and 0 <= usage[k] <= 10**10 for k in ("input_tokens", "output_tokens")))
                status = "reported" if reported else "rejected" if rejected else "uncertain" if failed else "estimated"
                inputs, outputs = ((usage["input_tokens"], usage["output_tokens"]) if reported else
                                   (0, 0) if rejected else (call.input_tokens, call.output_tokens))
                prices = dict(call.prices)
                if prices.get("reserved_searches"):
                    searches = usage.get("web_search_requests") if isinstance(usage, dict) else None
                    if rejected:
                        prices["searches"] = 0
                    elif type(searches) is int and 0 <= searches <= prices["reserved_searches"]:
                        prices["searches"] = searches
                    else:
                        status = "uncertain" if failed else "estimated"
                    call.prices = prices
                tokens, charge = inputs + outputs, cost(prices, inputs, outputs)
                changed = await session.execute(update(CodeUsagePolicy).where(
                    CodeUsagePolicy.chat_id == self.runtime.chat_id, CodeUsagePolicy.revision == row.revision
                ).values(tokens_used=CodeUsagePolicy.tokens_used + tokens - call.charged_tokens,
                         cost_used=CodeUsagePolicy.cost_used + charge - call.charged_cost,
                         revision=row.revision + 1))
                if changed.rowcount:
                    # The policy CAS serializes settlement, including repeated attempts.
                    call.status, call.input_tokens, call.output_tokens = status, inputs, outputs
                    call.charged_tokens, call.charged_cost = tokens, charge
                    call.settled_at = datetime.now(UTC)
                    await session.commit()
                    return
        # Retain the conservative reservation if reconciliation cannot commit.


def activate(runtime, job_id=None):
    return ACTIVE.set(Account(runtime, job_id))


def reset(token):
    ACTIVE.reset(token)


@asynccontextmanager
async def tracking(runtime, job_id=None):
    token = activate(runtime, job_id)
    try:
        yield
    finally:
        reset(token)
