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
# Переполнение контекста: «maximum context length is 32768 tokens», «prompt is too long» и т. п.
_CONTEXT = re.compile(
    r"context[ _]length|context window|context_length_exceeded|prompt is too long|input is too long"
    r"|too many (input )?tokens|reduce the length of the messages",
    re.IGNORECASE,
)
_CONTEXT_SIZE = re.compile(r"(?:maximum context length|context (?:length|window)(?: of| is)?)\D{0,20}(\d{3,8})",
                           re.IGNORECASE)
# Параметры, которые пользователь может задать модели. Если модель их не принимает —
# убираем из запроса (например, o1 не даёт менять temperature).
TUNABLE_PARAMS = ("temperature", "reasoning_effort")
_UNSUPPORTED_WORDS = ("unsupported", "not supported", "does not support", "only the default",
                      "unrecognized", "unknown", "extra inputs", "not permitted", "not allowed", "invalid")
_MAX_TOKENS = re.compile(r"max_(completion_)?tokens|max_output_tokens", re.IGNORECASE)
_TOO_LARGE_WORDS = ("too large", "less than or equal", "maximum", "exceed", "at most", "<=",
                    "range", "must be", "too big", "cannot be greater")
_TRANSIENT_WORDS = ("overload", "rate", "timeout", "unavailable", "server_error", "internal",
                    "capacity", "busy", "try again", "temporarily")


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, retry_after: float | None = None,
                 status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.status = status


class OutputLimitError(ProviderError):
    """Ответ обрезан лимитом длины (finish_reason=length / stop_reason=max_tokens)."""

    def __init__(self, message: str, *, has_calls: bool) -> None:
        super().__init__(message)
        self.has_calls = has_calls


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
    if _CONTEXT.search(body):
        found = _CONTEXT_SIZE.search(body) or re.search(r">\s*(\d{3,8})\s*maximum", body, re.IGNORECASE)
        return CapabilityError("История не помещается в контекст модели", capability="context",
                               value=int(found.group(1)) if found else None, status=status)
    for param in TUNABLE_PARAMS:
        if param in text and any(w in text for w in _UNSUPPORTED_WORDS):
            return CapabilityError(f"Модель не принимает параметр {param}", capability="drop",
                                   value=param, status=status)
    if "max_tokens" in text and "max_completion_tokens" in text:
        return CapabilityError("Модель требует max_completion_tokens", capability="token_param",
                               value="max_completion_tokens", status=status)
    if _MAX_TOKENS.search(text) and any(w in text for w in _TOO_LARGE_WORDS):
        # «max_tokens must be less than or equal to 8192» и т. п. — берём предел из текста.
        numbers = [int(n) for n in re.findall(r"\d{3,7}", text) if 256 <= int(n) <= 2_000_000]
        return CapabilityError("Провайдер ограничивает длину ответа", capability="max_output",
                               value=min(numbers) if numbers else None, status=status)
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
