"""Responses streaming adapter for subscription-token Grok calls."""
import json

import httpx

from app.services.grok_oauth import BASE_URL, wire_credentials
from app.services.provider_errors import OutputLimitError, ProviderError, http_error


def payload(model, messages, tools, caps):
    items = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": message["content"]})
            continue
        if message.get("content"):
            content = message["content"]
            if isinstance(content, list):
                converted = []
                for part in content:
                    if part.get("type") == "text":
                        converted.append({"type": "output_text" if role == "assistant" else "input_text", "text": part["text"]})
                    elif part.get("type") == "image_url":
                        converted.append({"type": "input_image", "image_url": part["image_url"]["url"]})
                    else:
                        raise ProviderError("Grok не поддерживает этот тип вложения")
                content = converted
            items.append({"role": role, "content": content})
        for call in message.get("tool_calls", []):
            items.append({"type": "function_call", "call_id": call["id"],
                          "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    result = {"model": model, "input": items, "stream": True, "store": False,
              "max_output_tokens": caps.get("max_output") or 8192}
    if tools:
        result["tools"] = [{"type": "function", **t["function"]} for t in tools]
        result["tool_choice"] = "auto"
    drop = caps.get("drop") or []
    if caps.get("temperature") is not None and "temperature" not in drop:
        result["temperature"] = caps["temperature"]
    if caps.get("reasoning_effort") and "reasoning_effort" not in drop:
        result["reasoning"] = {"effort": caps["reasoning_effort"]}
    return result


async def stream_turn(provider, key, model, messages, tools, caps=None):
    from app.services.provider_client import _headers

    if provider.base_url != BASE_URL:
        raise ProviderError("Некорректный адрес Grok")
    body = payload(model, messages, tools, caps or {})
    calls = {}
    completed = False
    size = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:
        async with client.stream("POST", BASE_URL + "/responses", headers=_headers(provider, key), json=body) as response:
            if response.is_error:
                text = (await response.aread())[:4000].decode("utf-8", "replace")
                raise http_error(response.status_code, text, response.headers, tools=bool(tools),
                                 secret=wire_credentials(key)["access_token"])
            async for line in response.aiter_lines():
                size += len(line.encode())
                if size > 4_000_000:
                    raise ProviderError("Ответ Grok превышает лимит")
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except ValueError:
                    raise ProviderError("Некорректный поток Grok") from None
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    yield "content", event.get("delta", "")
                elif kind in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
                    yield "reasoning", event.get("delta", "")
                elif kind == "response.output_item.added":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        calls[event["output_index"]] = dict(item)
                elif kind == "response.function_call_arguments.delta":
                    call = calls.get(event["output_index"])
                    if not call:
                        raise ProviderError("Grok прислал аргументы неизвестного инструмента")
                    call["arguments"] = call.get("arguments", "") + event.get("delta", "")
                elif kind == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        calls[event["output_index"]] = dict(item)
                elif kind == "response.completed":
                    result = event.get("response", {})
                    if result.get("status", "completed") != "completed":
                        raise ProviderError("Grok не завершил ответ")
                    # Final output is authoritative, including gateways without argument deltas.
                    output = result.get("output")
                    if output is not None:
                        calls = {i: dict(item) for i, item in enumerate(output) if item.get("type") == "function_call"}
                    completed = True
                    break
                elif kind == "response.incomplete":
                    reason = (event.get("response", {}).get("incomplete_details") or {}).get("reason")
                    if reason in (None, "max_output_tokens"):
                        raise OutputLimitError("Ответ Grok не поместился в лимит длины", has_calls=bool(calls))
                    raise ProviderError("Grok не завершил ответ")
                elif kind in ("error", "response.failed") or event.get("error"):
                    raise ProviderError("Grok прервал ответ ошибкой", retryable=True)
                if len(calls) > 48:
                    raise ProviderError("Grok запросил слишком много инструментов")
    if not completed:
        raise ProviderError("Связь с Grok оборвалась посреди ответа", retryable=True)
    batch = []
    for item in calls.values():
        arguments = item.get("arguments") or "{}"
        try:
            parsed = json.loads(arguments)
        except ValueError:
            raise ProviderError("Grok не завершил JSON инструмента") from None
        if not isinstance(parsed, dict) or not item.get("name") or not item.get("call_id"):
            raise ProviderError("Некорректный вызов инструмента Grok")
        batch.append({"id": item["call_id"], "type": "function",
                      "function": {"name": item["name"], "arguments": arguments}})
    ids = [c["id"] for c in batch]
    if len(batch) > 48 or len(set(ids)) != len(ids) or any(len(i) > 200 for i in ids):
        raise ProviderError("Некорректные идентификаторы инструментов Grok")
    if batch:
        yield "tool_calls", batch
