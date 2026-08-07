"""Local protocol adapter for one SiliconFlow OpenAI-compatible model."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from usage_logging import RequestContext, decode_request_context, record_usage


DEFAULT_MODEL = "THUDM/GLM-4-9B-0414"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
UPSTREAM_TIMEOUT_SECONDS = 180.0
MIN_REQUEST_INTERVAL_SECONDS = 3.0
MAX_RETRY_ATTEMPTS = 4
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_request_lock = asyncio.Lock()
_last_request_started_at = 0.0


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    local_token: str
    model: str
    usage_log_path: Path | None


def settings() -> Settings:
    api_key = os.environ.get("OA3_SILICONFLOW_API_KEY", "").strip()
    local_token = os.environ.get("EVAL_PROXY_TOKEN", "").strip()
    if not api_key or not local_token:
        raise RuntimeError("OA3_SILICONFLOW_API_KEY and EVAL_PROXY_TOKEN are required")
    return Settings(
        api_key=api_key,
        base_url=os.environ.get("SILICONFLOW_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        local_token=local_token,
        model=os.environ.get("EVAL_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        usage_log_path=(
            Path(path).expanduser()
            if (path := os.environ.get("EVAL_USAGE_LOG_PATH", "").strip())
            else None
        ),
    )


def require_local_token(request: Request) -> RequestContext | None:
    configured = settings().local_token
    authorization = request.headers.get("authorization", "")
    bearer = authorization.removeprefix("Bearer ").strip()
    supplied = bearer or request.headers.get("x-api-key", "").strip()
    return decode_request_context(supplied, configured)


def as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(as_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "output", "input"):
            if key in value:
                return as_text(value[key])
    return ""


def response_content_to_chat(content: object) -> str | list[dict[str, object]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return as_text(content)
    blocks: list[dict[str, object]] = []
    text: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            text.append(as_text(block))
            continue
        kind = str(block.get("type", ""))
        if kind in {"input_text", "output_text", "text"}:
            text.append(as_text(block.get("text")))
        elif kind in {"input_image", "image_url"}:
            image_url = as_text(block.get("image_url"))
            if image_url:
                blocks.append({"type": "image_url", "image_url": {"url": image_url}})
    if not blocks:
        return "".join(text)
    if text:
        blocks.insert(0, {"type": "text", "text": "".join(text)})
    return blocks


def response_item_messages(item: dict[str, object]) -> list[dict[str, object]]:
    kind = str(item.get("type", ""))
    if kind == "message":
        role = str(item.get("role", "user"))
        if role == "developer":
            role = "system"
        return [{"role": role, "content": response_content_to_chat(item.get("content", ""))}]
    if kind in {"function_call", "custom_tool_call"}:
        call_id = as_text(item.get("call_id")) or f"call_{uuid4().hex}"
        name = as_text(item.get("name"))
        arguments = as_text(item.get("arguments"))
        if kind == "custom_tool_call":
            arguments = json.dumps({"input": as_text(item.get("input"))})
        return [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments or "{}"},
                    }
                ],
            }
        ]
    if kind in {"function_call_output", "custom_tool_call_output"}:
        return [
            {
                "role": "tool",
                "tool_call_id": as_text(item.get("call_id")),
                "content": as_text(item.get("output")),
            }
        ]
    return []


def response_input_to_chat(input_value: object) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}], []
    if not isinstance(input_value, list):
        return [], []
    messages: list[dict[str, object]] = []
    embedded_tools: list[dict[str, object]] = []
    for raw_item in input_value:
        if not isinstance(raw_item, dict):
            continue
        if raw_item.get("type") == "additional_tools":
            tools = raw_item.get("tools")
            if isinstance(tools, list):
                embedded_tools.extend(tool for tool in tools if isinstance(tool, dict))
            continue
        messages.extend(response_item_messages(raw_item))
    return messages, embedded_tools


def response_tools_to_chat(tools: object) -> tuple[list[dict[str, object]], set[str]]:
    if not isinstance(tools, list):
        return [], set()
    translated: list[dict[str, object]] = []
    custom_names: set[str] = set()
    for raw_tool in tools:
        if not isinstance(raw_tool, dict):
            continue
        name = as_text(raw_tool.get("name"))
        if not name:
            continue
        kind = as_text(raw_tool.get("type"))
        if kind not in {"function", "custom"}:
            continue
        if kind == "custom":
            custom_names.add(name)
            parameters: dict[str, object] = {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
            }
        else:
            parameters = raw_tool.get("parameters") if isinstance(raw_tool.get("parameters"), dict) else {}
        translated.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": as_text(raw_tool.get("description")),
                    "parameters": parameters,
                },
            }
        )
    return translated, custom_names


def anthropic_messages_to_chat(payload: dict[str, object]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    system = payload.get("system")
    if system:
        messages.append({"role": "system", "content": as_text(system)})
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        return messages
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        role = as_text(raw_message.get("role")) or "user"
        content = raw_message.get("content", "")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        text_blocks: list[str] = []
        tool_calls: list[dict[str, object]] = []
        tool_results: list[dict[str, object]] = []
        for block in blocks:
            if not isinstance(block, dict):
                text_blocks.append(as_text(block))
                continue
            kind = as_text(block.get("type"))
            if kind == "tool_use":
                tool_calls.append(
                    {
                        "id": as_text(block.get("id")) or f"call_{uuid4().hex}",
                        "type": "function",
                        "function": {
                            "name": as_text(block.get("name")),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    }
                )
            elif kind == "tool_result":
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": as_text(block.get("tool_use_id")),
                        "content": as_text(block.get("content")),
                    }
                )
            elif kind == "image":
                continue
            else:
                text_blocks.append(as_text(block.get("text", block)))
        if text_blocks or tool_calls:
            message: dict[str, object] = {"role": role, "content": "".join(text_blocks)}
            if tool_calls:
                message["tool_calls"] = tool_calls
            messages.append(message)
        messages.extend(tool_results)
    return messages


def anthropic_tools_to_chat(tools: object) -> list[dict[str, object]]:
    if not isinstance(tools, list):
        return []
    translated: list[dict[str, object]] = []
    for raw_tool in tools:
        if not isinstance(raw_tool, dict):
            continue
        name = as_text(raw_tool.get("name"))
        if not name:
            continue
        schema = raw_tool.get("input_schema")
        translated.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": as_text(raw_tool.get("description")),
                    "parameters": schema if isinstance(schema, dict) else {},
                },
            }
        )
    return translated


def anthropic_tool_choice(value: object) -> object | None:
    if not isinstance(value, dict):
        return None
    kind = as_text(value.get("type"))
    if kind == "any":
        return "required"
    if kind == "tool" and as_text(value.get("name")):
        return {"type": "function", "function": {"name": as_text(value.get("name"))}}
    if kind == "auto":
        return "auto"
    return None


async def wait_for_request_slot() -> None:
    global _last_request_started_at
    async with _request_lock:
        elapsed = time.monotonic() - _last_request_started_at
        delay = max(0.0, MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        if delay:
            await asyncio.sleep(delay)
        _last_request_started_at = time.monotonic()


def retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after", "").strip()
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 120.0))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                return max(0.0, min((retry_at - datetime.now(UTC)).total_seconds(), 120.0))
            except (TypeError, ValueError, OverflowError):
                pass
    if response.status_code == 429:
        return 60.0
    return min(2.0**attempt, 30.0)


async def chat_completion(
    messages: list[dict[str, object]],
    tools: list[dict[str, object]],
    options: dict[str, object],
) -> dict[str, object]:
    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"message": "No input messages"}})
    active = settings()
    payload: dict[str, object] = {"model": active.model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
    for key in ("temperature", "top_p", "max_tokens", "presence_penalty", "frequency_penalty", "stop"):
        value = options.get(key)
        if value is not None:
            payload[key] = value
    for key in ("tool_choice", "parallel_tool_calls"):
        value = options.get(key)
        if value is not None and tools:
            payload[key] = value
    headers = {"authorization": f"Bearer {active.api_key}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS, trust_env=False) as client:
        for attempt in range(MAX_RETRY_ATTEMPTS + 1):
            await wait_for_request_slot()
            try:
                upstream = await client.post(
                    f"{active.base_url}/chat/completions", headers=headers, json=payload
                )
            except httpx.HTTPError as error:
                if attempt == MAX_RETRY_ATTEMPTS:
                    raise HTTPException(
                        status_code=502,
                        detail={
                            "error": {
                                "message": f"Upstream transport error: {error.__class__.__name__}"
                            }
                        },
                    ) from error
                await asyncio.sleep(min(2.0**attempt, 30.0))
                continue
            if upstream.status_code not in RETRYABLE_STATUS_CODES:
                break
            if attempt == MAX_RETRY_ATTEMPTS:
                break
            await asyncio.sleep(retry_delay_seconds(upstream, attempt))
    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail={"error": {"message": "Upstream model request failed"}})
    data = upstream.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail={"error": {"message": "Invalid upstream response"}})
    return data


def response_usage(data: dict[str, object]) -> dict[str, object]:
    raw_usage = data.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    input_tokens = int(usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": None,
        "output_tokens": output_tokens,
        "output_tokens_details": None,
        "total_tokens": total_tokens,
    }


def choice_message(data: dict[str, object]) -> tuple[dict[str, object], str]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise HTTPException(status_code=502, detail={"error": {"message": "Upstream response has no choices"}})
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise HTTPException(status_code=502, detail={"error": {"message": "Upstream choice has no message"}})
    return message, as_text(choices[0].get("finish_reason"))


def response_items(message: dict[str, object], custom_names: set[str]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    text = as_text(message.get("content"))
    if text:
        output.append(
            {
                "id": f"msg_{uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return output
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        name = as_text(function.get("name"))
        arguments = as_text(function.get("arguments")) or "{}"
        call_id = as_text(tool_call.get("id")) or f"call_{uuid4().hex}"
        if name in custom_names:
            try:
                input_value = json.loads(arguments).get("input", arguments)
            except json.JSONDecodeError:
                input_value = arguments
            output.append(
                {
                    "id": f"ctc_{uuid4().hex}",
                    "type": "custom_tool_call",
                    "call_id": call_id,
                    "name": name,
                    "input": as_text(input_value),
                }
            )
        else:
            output.append(
                {
                    "id": f"fc_{uuid4().hex}",
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                }
            )
    return output


def response_object(data: dict[str, object], output: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": f"resp_{uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": settings().model,
        "output": output,
        "usage": response_usage(data),
        "error": None,
    }


def sse_event(kind: str, payload: dict[str, object]) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def response_stream(response: dict[str, object]) -> AsyncIterator[str]:
    response_id = as_text(response.get("id"))
    yield sse_event("response.created", {"type": "response.created", "response": {"id": response_id}})
    for index, item in enumerate(response.get("output", [])):
        if isinstance(item, dict):
            yield sse_event(
                "response.output_item.done",
                {"type": "response.output_item.done", "output_index": index, "item": item},
            )
    yield sse_event("response.completed", {"type": "response.completed", "response": response})


def anthropic_response(data: dict[str, object], message: dict[str, object], finish_reason: str) -> dict[str, object]:
    blocks: list[dict[str, object]] = []
    text = as_text(message.get("content"))
    if text:
        blocks.append({"type": "text", "text": text})
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict) or not isinstance(tool_call.get("function"), dict):
                continue
            function = tool_call["function"]
            arguments = as_text(function.get("arguments")) or "{}"
            try:
                tool_input = json.loads(arguments)
            except json.JSONDecodeError:
                tool_input = {"input": arguments}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": as_text(tool_call.get("id")) or f"toolu_{uuid4().hex}",
                    "name": as_text(function.get("name")),
                    "input": tool_input,
                }
            )
    stop_reason = "tool_use" if any(block.get("type") == "tool_use" for block in blocks) else "end_turn"
    if finish_reason == "length" and stop_reason == "end_turn":
        stop_reason = "max_tokens"
    usage = response_usage(data)
    return {
        "id": f"msg_{uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "content": blocks,
        "model": settings().model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]},
    }


async def anthropic_stream(response: dict[str, object]) -> AsyncIterator[str]:
    usage = response["usage"] if isinstance(response.get("usage"), dict) else {}
    start = dict(response)
    start["content"] = []
    start["stop_reason"] = None
    start["usage"] = {"input_tokens": usage.get("input_tokens", 0), "output_tokens": 0}
    yield sse_event("message_start", {"type": "message_start", "message": start})
    content = response.get("content")
    blocks = content if isinstance(content, list) else []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        kind = as_text(block.get("type"))
        if kind == "text":
            initial = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": as_text(block.get("text"))}
        else:
            initial = {"type": "tool_use", "id": block.get("id"), "name": block.get("name"), "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block.get("input", {}), ensure_ascii=False)}
        yield sse_event("content_block_start", {"type": "content_block_start", "index": index, "content_block": initial})
        yield sse_event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
        yield sse_event("content_block_stop", {"type": "content_block_stop", "index": index})
    yield sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": response.get("stop_reason"), "stop_sequence": None},
            "usage": {"output_tokens": usage.get("output_tokens", 0)},
        },
    )
    yield sse_event("message_stop", {"type": "message_stop"})


@app.get("/health/liveliness")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/v1/models")
async def models(request: Request) -> dict[str, object]:
    require_local_token(request)
    return {"object": "list", "data": [{"id": settings().model, "object": "model"}]}


@app.post("/v1/chat/completions")
async def openai_chat(request: Request) -> JSONResponse:
    context = require_local_token(request)
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise HTTPException(status_code=400, detail={"error": {"message": "messages must be a list"}})
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    upstream = await chat_completion(payload["messages"], tools, payload)
    configured = settings()
    record_usage(
        context,
        "chat.completions",
        response_usage(upstream),
        configured.usage_log_path,
        configured.model,
    )
    return JSONResponse(upstream)


@app.post("/v1/responses", response_model=None)
@app.post("/responses", response_model=None)
async def openai_responses(request: Request) -> JSONResponse | StreamingResponse:
    context = require_local_token(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={"error": {"message": "Request body must be an object"}})
    messages, embedded_tools = response_input_to_chat(payload.get("input"))
    instructions = as_text(payload.get("instructions"))
    if instructions:
        messages.insert(0, {"role": "system", "content": instructions})
    top_level_tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    tools, custom_names = response_tools_to_chat([*embedded_tools, *top_level_tools])
    upstream = await chat_completion(messages, tools, payload)
    configured = settings()
    record_usage(
        context,
        "responses",
        response_usage(upstream),
        configured.usage_log_path,
        configured.model,
    )
    message, _ = choice_message(upstream)
    response = response_object(upstream, response_items(message, custom_names))
    if payload.get("stream"):
        return StreamingResponse(response_stream(response), media_type="text/event-stream")
    return JSONResponse(response)


@app.post("/v1/messages", response_model=None)
async def anthropic_messages(request: Request) -> JSONResponse | StreamingResponse:
    context = require_local_token(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={"error": {"message": "Request body must be an object"}})
    messages = anthropic_messages_to_chat(payload)
    tools = anthropic_tools_to_chat(payload.get("tools"))
    options = dict(payload)
    if "max_tokens" in payload:
        options["max_tokens"] = payload["max_tokens"]
    if "stop_sequences" in payload:
        options["stop"] = payload["stop_sequences"]
    options["tool_choice"] = anthropic_tool_choice(payload.get("tool_choice"))
    upstream = await chat_completion(messages, tools, options)
    configured = settings()
    record_usage(
        context,
        "messages",
        response_usage(upstream),
        configured.usage_log_path,
        configured.model,
    )
    message, finish_reason = choice_message(upstream)
    response = anthropic_response(upstream, message, finish_reason)
    if payload.get("stream"):
        return StreamingResponse(anthropic_stream(response), media_type="text/event-stream")
    return JSONResponse(response)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=4000, access_log=False)
