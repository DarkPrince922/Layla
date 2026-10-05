"""Telegram Bot API transport; secrets never appear in public errors or logs."""
import logging
import httpx

# httpx INFO request URLs would expose /bot<TOKEN>/ paths.
logging.getLogger("httpx").setLevel(logging.WARNING)


class TelegramError(RuntimeError):
    def __init__(self, message, *, retry_after=15):
        super().__init__(message)
        self.retry_after = retry_after


async def call(token, method, body=None, *, timeout=15, files=None):
    if method not in {"getMe", "getWebhookInfo", "getUpdates", "sendMessage", "sendDocument", "answerCallbackQuery", "setMyCommands"}:
        raise ValueError("Unsupported Telegram method")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"https://api.telegram.org/bot{token}/{method}",
                **({"data": body, "files": files} if files else {"json": body or {}}))
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise TelegramError("Telegram недоступен. Повторим позже.") from None
    if response.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
        code = data.get("error_code", response.status_code) if isinstance(data, dict) else response.status_code
        parameters = data.get("parameters") if isinstance(data, dict) else None
        try:
            delay = int(parameters.get("retry_after", 15)) if isinstance(parameters, dict) else 15
        except (ValueError, TypeError):
            delay = 15
        raise TelegramError(f"Telegram API: ошибка {code}", retry_after=max(1, min(int(delay), 3600)))
    return data.get("result")


def chunks(text, limit=3800):
    result, part, units = [], [], 0
    for char in text or "—":
        count = 2 if ord(char) > 0xFFFF else 1
        if units + count > limit:
            result.append("".join(part)); part, units = [], 0
        part.append(char); units += count
    if part:
        result.append("".join(part))
    return result


async def send_message(token, chat_id, text, *, timeout=15, reply_markup=None):
    parts = chunks(text)
    for index, part in enumerate(parts):
        body = {"chat_id": chat_id, "text": part}
        if index == len(parts) - 1 and reply_markup:
            body["reply_markup"] = reply_markup
        await call(token, "sendMessage", body, timeout=timeout)


async def send_document(token, chat_id, content, filename="result.txt"):
    raw = content.encode() if isinstance(content, str) else content
    if len(raw) > 20_000_000:
        raise TelegramError("Файл больше 20 МБ. Скачайте его через веб-интерфейс.")
    await call(token, "sendDocument", {"chat_id": chat_id}, timeout=60,
               files={"document": (filename, raw, "application/octet-stream")})
