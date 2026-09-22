"""Классификация ошибок провайдера: что повторить, а что означает «модель это не умеет».

Повторяем только временные сбои (обрыв связи, 429, 5xx) — до 5 раз с растущей
паузой. Ошибки запроса (400/401/403) повтор не исправит. Отдельный случай —
отказ из-за неподдерживаемой части запроса (инструменты, max_tokens,
reasoning_content): такой запрос можно сразу повторить без неё.
"""

from __future__ import annotations

import re

import httpx

RETRY_DELAYS = (1, 2, 4, 8, 16)
MAX_RETRY_DELAY = 30
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})

# Модель не умеет вызывать инструменты (Ollama, vLLM без --enable-auto-tool-choice,
# OpenRouter без подходящих эндпоинтов и т. п.).
_NO_TOOLS = re.compile(
    r"(does not|doesn't|do not) support (tools|tool use|tool calling|function)"
    r"|(tools?|tool use|tool calling|function calling|tool_choice)[^.]{0,40}"
    r"(not supported|unsupported|not enabled|is disabled)"
    r"|support tool use|enable-auto-tool-choice",
    re.IGNORECASE,
)
_TRANSIENT_WORDS = ("overload", "rate", "timeout", "unavailable", "server_error", "internal",
                    "capacity", "busy", "try again", "temporarily")


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, retry_after: float | None = None,
                 status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.status = status


class CapabilityError(ProviderError):
    """Модель отклонила часть запроса; повторить с caps[capability] = value."""

    def __init__(self, message: str, *, capability: str, value, status: int | None = None) -> None:
        super().__init__(message, status=status)
        self.capability = capability
        self.value = value


def classify_rejection(status: int, body: str) -> CapabilityError | None:
    if status not in (400, 404, 422):
        return None
    text = body.lower()
    if "max_tokens" in text and "max_completion_tokens" in text:
        return CapabilityError("Модель требует max_completion_tokens", capability="token_param",
                               value="max_completion_tokens", status=status)
    if "reasoning_content" in text:
        return CapabilityError("Провайдер не принимает reasoning_content", capability="replay_reasoning",
                               value=False, status=status)
    if _NO_TOOLS.search(body):
        return CapabilityError("Модель не поддерживает инструменты", capability="tools",
                               value=False, status=status)
    return None


def retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def transient_payload(payload) -> bool:
    return any(word in str(payload).lower() for word in _TRANSIENT_WORDS)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, CapabilityError):
        return False
    if isinstance(exc, ProviderError):
        return exc.retryable
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)  # обрыв, сброс соединения, таймауты


def retry_delay(exc: BaseException, attempt: int) -> float:
    base = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]
    hinted = getattr(exc, "retry_after", None) or 0
    return float(min(max(base, hinted), MAX_RETRY_DELAY))
