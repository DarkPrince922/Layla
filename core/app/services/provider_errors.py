"""Классификация ошибок провайдера: что повторить, а что означает «модель это не умеет».

Повторяем только временные сбои (обрыв связи, 429, 5xx) — до 5 раз с растущей
паузой. Ошибки запроса (400/401/403) повтор не исправит. Отдельный случай —
отказ из-за неподдерживаемой части запроса (инструменты, max_tokens,
reasoning_content): такой запрос можно сразу повторить без неё.
"""

from __future__ import annotations

import json
import re

import httpx

RETRY_DELAYS = (1, 2, 4, 8, 16)
MAX_RETRY_DELAY = 60  # Retry-After больше минуты не ждём
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})
# Отказ из-за ключа: неверный, без денег, без доступа, упёрся в лимит частоты.
# Если у провайдера несколько ключей, такой запрос сразу повторяем с другим.
KEY_STATUS = frozenset({401, 402, 403, 429})

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
# Сервер требует строку в content и не принимает null у хода только с вызовом инструмента.
_NULL_CONTENT = re.compile(
    r"content\W[^.]{0,60}(null|none|valid string|must be (a )?string|expected (a )?string|str type expected)",
    re.IGNORECASE,
)
_MAX_TOKENS = re.compile(r"max_(completion_)?tokens|max_output_tokens", re.IGNORECASE)
_TOO_LARGE_WORDS = ("too large", "less than or equal", "maximum", "exceed", "at most", "<=",
                    "range", "must be", "too big", "cannot be greater")
# Временный сбой по тексту ошибки. Шлюзы вроде OpenRouter отвечают 400 «Provider returned
# error», когда упал выбранный ими апстрим, — повтор уходит на другой и обычно проходит.
_TRANSIENT = re.compile(
    r"provider returned error|upstream|overload|temporar|try again|timed? ?out|timeout"
    r"|rate.?limit|too many requests|capacity|unavailable|server_error|internal (server )?error|\bbusy\b",
    re.IGNORECASE,
)
# Всё, похожее на ключ или токен, из текста ошибки убираем.
_SECRET = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|\b[A-Za-z0-9]{32,}\b")


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, retry_after: float | None = None,
                 status: int | None = None, key_failed: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.status = status
        self.key_failed = key_failed  # поможет другой ключ того же провайдера


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
    if _NULL_CONTENT.search(body):
        return CapabilityError("Провайдер требует строку в content", capability="tool_content",
                               value="", status=status)
    if _NO_TOOLS.search(body):
        return CapabilityError("Модель не поддерживает инструменты", capability="tools",
                               value=False, status=status)
    return None


def provider_message(body: str, limit: int = 300, *, raw: bool = False, secret: str | None = None) -> str:
    """Причина отказа из ответа провайдера (OpenAI, Anthropic, OpenRouter, LiteLLM) — без секретов.

    Пользователю показываем только поле сообщения из JSON-ошибки API. Произвольный
    текст или HTML (страница прокси, мусор) — только в лог сервера (raw=True).
    """
    text = ""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        error = data.get("error", data)
        if isinstance(error, dict):
            text = str(error.get("message") or error.get("detail") or error.get("msg") or "")
            meta = error.get("metadata")
            if isinstance(meta, dict):
                # OpenRouter кладёт настоящую причину апстрима в metadata.raw.
                raw = meta.get("raw")
                inner = (provider_message(raw if isinstance(raw, str) else json.dumps(raw), limit, raw=True,
                                          secret=secret) if raw else "")
                if inner and inner not in text:
                    text = f"{text}: {inner}" if text else inner
                if meta.get("provider_name"):
                    text = f"{text} [{meta['provider_name']}]"
        elif error:
            text = str(error)
        if not text and data.get("detail"):
            text = str(data["detail"])
    if not text and body and raw:
        text = re.sub(r"<[^>]+>", " ", str(body))  # HTML-страница ошибки прокси
    if secret and len(secret) >= 6:
        text = text.replace(secret, "…")
    text = _SECRET.sub("…", " ".join(text.split()))
    return text[:limit] + ("…" if len(text) > limit else "")


def http_error(status: int, body: str, headers: httpx.Headers | None = None, *,
               tools: bool = False, secret: str | None = None) -> ProviderError:
    """Понятная ошибка по коду ответа и причине от провайдера."""
    reason = provider_message(body, secret=secret)
    suffix = f": {reason}" if reason else ""
    retry = status in RETRYABLE_STATUS or bool(_TRANSIENT.search(body or ""))
    if status == 401:
        message = f"Провайдер не принял ключ (HTTP 401){suffix}. Проверьте ключ в «Настройки → Провайдеры»."
        retry = False
    elif status == 402:
        message = f"У провайдера закончились средства или квота (HTTP 402){suffix}"
        retry = False
    elif status == 403:
        message = f"Провайдер отказал в доступе (HTTP 403){suffix}"
        retry = False
    elif status == 429:
        wait = retry_after(headers) if headers else None
        if wait and wait > 300:
            # Исчерпана дневная/месячная квота — ждать внутри задачи бессмысленно.
            message = (f"Провайдер исчерпал лимит запросов (HTTP 429){suffix}. "
                       f"Снова будет доступен примерно через {round(wait / 60)} мин — или добавьте ещё один ключ.")
            retry = False
        else:
            message = (f"Провайдер ограничил частоту запросов (HTTP 429){suffix}. "
                       "Подождите минуту или добавьте ещё один ключ.")
    elif status == 404:
        message = f"Провайдер не нашёл модель или адрес (HTTP 404){suffix}. Проверьте имя модели и адрес API."
    elif status >= 500:
        message = f"Сбой на стороне провайдера (HTTP {status}){suffix}"
        retry = status not in (501, 505)
    else:
        what = "запрос с инструментами" if tools else "запрос"
        message = f"Провайдер отклонил {what} (HTTP {status}){suffix}"
    return ProviderError(message, retryable=retry, retry_after=retry_after(headers) if headers else None,
                         status=status, key_failed=status in KEY_STATUS)


def retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def transient_payload(payload) -> bool:
    return bool(_TRANSIENT.search(str(payload)))


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
