"""Streaming tool-call protocol for OpenAI-compatible and native Anthropic APIs.

A call is exposed only after the provider completes the entire turn. Truncated
JSON, interrupted streams and token-limit stops never execute partial writes.
The internal conversation uses OpenAI messages; Anthropic conversion is explicit.
"""

from __future__ import annotations

import json

import httpx

from app.services.provider_client import _headers, _is_anthropic_native, endpoint

MAX_TURN_BYTES = 4_000_000


def anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    converted = []
    for message in messages:
        role = message["role"]
        if role == "system":
            continue
        blocks = []
        if role == "tool":
            role = "user"
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message["content"],
                    "is_error": message.get("is_error", False),
                }
            )
        else:
            blocks.extend(message.get("_anthropic_thinking", []))
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls", []):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": json.loads(call["function"]["arguments"]),
                    }
                )
        if not blocks:
            continue
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    return system, converted


async def stream_turn(provider, key, model: str, messages: list[dict], tools: list[dict]):
    native = _is_anthropic_native(provider)
    if native:
        system, conversation = anthropic_messages(messages)
        payload = {
            "model": model,
            "system": system,
            "messages": conversation,
            "stream": True,
            "max_tokens": 8192,
            "tools": [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ],
        }
        url = endpoint(provider, "messages")
    else:
        payload = {
            "model": model,
            "messages": [{k: v for k, v in m.items() if k != "is_error" and not k.startswith("_")} for m in messages],
            "stream": True,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 8192,
        }
        url = endpoint(provider, "chat/completions")

    if not tools:
        payload.pop("tools", None)
        payload.pop("tool_choice", None)

    calls: dict[int, dict] = {}
    initial_inputs: dict[int, dict] = {}
    thinking: dict[int, dict] = {}
    finish = None
    stopped = False
    size = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:  # noqa: SIM117
        async with client.stream(
            "POST", url, headers=_headers(provider, key), json=payload
        ) as resp:
            if resp.is_error:
                raise RuntimeError(
                    f"Провайдер отклонил запрос инструментов (HTTP {resp.status_code})"
                )
            async for line in resp.aiter_lines():
                size += len(line.encode("utf-8"))
                if size > MAX_TURN_BYTES:
                    raise RuntimeError("Ответ провайдера превышает лимит")
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    stopped = True
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Некорректный поток провайдера") from exc
                if event.get("error") or event.get("type") == "error":
                    raise RuntimeError("Провайдер вернул ошибку при работе с инструментами")
                if native:
                    kind = event.get("type")
                    index = event.get("index", 0)
                    if kind == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") in ("thinking", "redacted_thinking"):
                            thinking[index] = dict(block)
                        if block.get("type") == "tool_use":
                            initial_inputs[index] = block.get("input") or {}
                            calls[index] = {
                                "id": block["id"],
                                "type": "function",
                                "function": {"name": block["name"], "arguments": ""},
                            }
                    elif kind == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "input_json_delta":
                            calls[index]["function"]["arguments"] += delta.get("partial_json", "")
                        elif delta.get("text"):
                            yield "content", delta["text"]
                        elif delta.get("thinking"):
                            block = thinking.setdefault(index, {"type": "thinking", "thinking": "", "signature": ""})
                            block["thinking"] = block.get("thinking", "") + delta["thinking"]
                            yield "reasoning", delta["thinking"]
                        elif delta.get("type") == "signature_delta" and index in thinking:
                            thinking[index]["signature"] = thinking[index].get("signature", "") + delta.get("signature", "")
                    elif kind == "message_delta":
                        finish = event.get("delta", {}).get("stop_reason")
                    elif kind == "message_stop":
                        stopped = True
                        break
                else:
                    choices = event.get("choices") or []
                    if not choices:
                        continue  # e.g. usage-only chunk
                    choice = choices[0]
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}
                    for part in delta.get("tool_calls") or []:
                        index = part.get("index", 0)
                        call = calls.setdefault(
                            index,
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        # Some compatible gateways repeat id/name in every chunk.
                        for target, field, value in (
                            (call, "id", part.get("id") or ""),
                            (call["function"], "name", part.get("function", {}).get("name") or ""),
                        ):
                            old = target[field]
                            if value and value != old:
                                target[field] = value if value.startswith(old) else old + value
                        call["function"]["arguments"] += part.get("function", {}).get("arguments") or ""
                    if delta.get("content"):
                        yield "content", delta["content"]
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield "reasoning", reasoning
                if len(calls) > 48:
                    raise RuntimeError("Провайдер запросил слишком много инструментов")
    allowed = ("tool_use", "end_turn", "stop_sequence") if native else ("tool_calls", "stop")
    if not stopped or finish not in allowed:
        raise RuntimeError(
            "Ответ модели прерван или достиг лимита. Незавершённые вызовы не применены."
        )
    if thinking:
        yield "provider_context", {"_anthropic_thinking": [thinking[i] for i in sorted(thinking)]}
    if calls:
        if finish not in (("tool_use",) if native else ("tool_calls", "stop")):
            raise RuntimeError("Провайдер не завершил вызов инструментов")
        for index, call in calls.items():
            if not call["function"]["arguments"] and index in initial_inputs:
                call["function"]["arguments"] = json.dumps(initial_inputs[index], ensure_ascii=False)
        result = [calls[i] for i in sorted(calls)]
        ids = [c["id"] for c in result]
        if len(set(ids)) != len(ids) or any(not i or len(i) > 200 for i in ids):
            raise RuntimeError("Некорректные идентификаторы инструментов")
        # Validate the entire batch before exposing any call for execution.
        for call in result:
            arguments = call["function"]["arguments"] or "{}"
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Модель не завершила JSON инструмента") from exc
            if not isinstance(parsed, dict):
                raise RuntimeError("Ожидался JSON-объект аргументов инструмента")  # noqa: TRY004 — provider protocol error
            call["function"]["arguments"] = arguments
        yield "tool_calls", result
