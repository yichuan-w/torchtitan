#!/usr/bin/env python3
"""Local OpenAI chat and Responses facade for MetaGen models.

The public TMax evolution code can use either its one-shot chat arm or Codex's
Responses client. MetaGen owns the provider request: this process translates
the wire format, submits in foreground or background mode, and streams the
completed output back to Codex. Background submissions are polled to completion.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LOG = logging.getLogger("metagen_chat_proxy")
_MAX_REQUEST_BYTES = 32 * 1024 * 1024
_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED"}
_SUBMISSION_MODES = {"background", "foreground"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _state_name(response: object) -> str | None:
    state = getattr(response, "processing_state", None)
    return getattr(state, "name", None) if state is not None else None


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise TypeError("message content must be a string or a list")
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") in {
            "text",
            "input_text",
            "output_text",
        }:
            parts.append(str(part.get("text", "")))
        elif isinstance(part, dict):
            # Non-text parts (an image from a tool, a web-search result) cannot go
            # through this text-only route; keep the session alive with a marker
            # instead of failing the whole rewrite.
            kind = part.get("type", "unknown")
            print(f"metagen_chat_proxy: dropped non-text content part {kind!r}", flush=True)
            parts.append(f"[{kind} content omitted: this route accepts text only]")
        else:
            raise ValueError("only text message content is supported")
    return "\n".join(parts)


def _choice_messages(response: object) -> list[object]:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise RuntimeError("MetaGen completed without a choice")
    messages = getattr(getattr(choices[0], "dialog", None), "messages", None) or []
    if not messages:
        raise RuntimeError("MetaGen completed without a message")
    return messages


def _parse_tool_call_text(value: str) -> list[dict[str, str]]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MetaGen returned malformed tool-call JSON") from exc
    if isinstance(parsed, dict) and isinstance(parsed.get("tool_calls"), list):
        parsed = parsed["tool_calls"]
    records = parsed if isinstance(parsed, list) else [parsed]
    result: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            raise TypeError("MetaGen returned a non-object tool call")
        nested = record.get("function")
        if not isinstance(nested, dict):
            nested = record.get("functionCall")
        source = nested if isinstance(nested, dict) else record
        name = source.get("name")
        if not isinstance(name, str) or not name:
            raise RuntimeError("MetaGen returned a tool call without a name")
        arguments: object = source.get("arguments")
        if arguments is None:
            arguments = source.get("args")
        if arguments is None:
            arguments = source.get("input")
        if arguments is None:
            arguments = source.get("parameters")
        if isinstance(arguments, str):
            arguments_json = arguments
        else:
            arguments_json = json.dumps(arguments if arguments is not None else {})
        call_id = record.get("call_id") or record.get("id") or _new_id("call")
        result.append(
            {
                "id": str(record.get("id") or _new_id("fc")),
                "type": "function_call",
                "status": "completed",
                "call_id": str(call_id),
                "name": name,
                "arguments": arguments_json,
            }
        )
    return result


def _response_output_items(response: object) -> list[dict[str, Any]]:
    messages = _choice_messages(response)
    contents = getattr(messages[-1], "contents", None) or []
    calls: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for content in contents:
        arguments = getattr(content, "arguments", None)
        call_id = getattr(content, "call_id", None)
        name = getattr(content, "name", None)
        if arguments is not None and call_id is not None and name is not None:
            calls.append(
                {
                    "id": str(getattr(content, "id", None) or _new_id("fc")),
                    "type": "function_call",
                    "status": "completed",
                    "call_id": str(call_id or _new_id("call")),
                    "name": str(name),
                    "arguments": str(arguments or "{}"),
                }
            )
            continue
        tool_call_text = getattr(content, "tool_call_text", None)
        if tool_call_text is not None:
            calls.extend(_parse_tool_call_text(str(tool_call_text)))
            continue
        answer = getattr(content, "answer", None)
        text = getattr(content, "text", None)
        if answer:
            text_parts.append(str(answer))
        elif text:
            text_parts.append(str(text))
    result = list(calls)
    if text_parts:
        text = "\n".join(text_parts)
        result.append(
            {
                "id": _new_id("msg"),
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )
    if not result:
        raise RuntimeError("MetaGen completed without text or tool-call content")
    return result


def _response_text(response: object) -> str:
    for item in reversed(_response_output_items(response)):
        if item["type"] != "message":
            continue
        return "\n".join(str(part.get("text", "")) for part in item.get("content", []))
    raise RuntimeError("MetaGen completed without text content")


def _finish_reason(response: object) -> str:
    choices = getattr(response, "choices", None) or []
    reason = getattr(choices[0], "finish_reason", None) if choices else None
    name = getattr(reason, "name", "")
    return "length" if name in {"MAX_OUTPUT_TOKENS", "PROMPT_TOO_LONG"} else "stop"


def _chat_usage(response: object) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    result: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(getattr(usage, "total_tokens", 0) or prompt + completion),
    }
    reasoning = getattr(usage, "num_reasoning_tokens", None)
    cached = getattr(usage, "num_cached_prompt_tokens", None)
    if reasoning is not None:
        result["completion_tokens_details"] = {"reasoning_tokens": int(reasoning)}
    if cached is not None:
        result["prompt_tokens_details"] = {"cached_tokens": int(cached)}
    return result


def _responses_usage(response: object) -> dict[str, Any]:
    chat = _chat_usage(response)
    return {
        "input_tokens": chat["prompt_tokens"],
        "input_tokens_details": {
            "cached_tokens": chat.get("prompt_tokens_details", {}).get(
                "cached_tokens", 0
            )
        },
        "output_tokens": chat["completion_tokens"],
        "output_tokens_details": {
            "reasoning_tokens": chat.get("completion_tokens_details", {}).get(
                "reasoning_tokens", 0
            )
        },
        "total_tokens": chat["total_tokens"],
    }


def _responses_shell(
    payload: dict[str, Any],
    response_id: str,
    *,
    status: str,
    output: list[dict[str, Any]],
    usage: dict[str, Any] | None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": payload.get("model"),
        "output": output,
        "usage": usage,
        "metadata": {},
        "temperature": payload.get("temperature", 1.0),
        "top_p": payload.get("top_p", 1.0),
        "max_output_tokens": payload.get("max_output_tokens"),
        "tools": payload.get("tools") or [],
        "tool_choice": payload.get("tool_choice", "auto"),
        "truncation": payload.get("truncation", "disabled"),
        "parallel_tool_calls": bool(payload.get("parallel_tool_calls", True)),
        "incomplete_details": None,
        "error": error,
    }


def _responses_events(
    initial: dict[str, Any], final: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    sequence_number = 0

    def event(type_: str, **values: Any) -> dict[str, Any]:
        nonlocal sequence_number
        result = {"type": type_, "sequence_number": sequence_number, **values}
        sequence_number += 1
        return result

    yield event("response.created", response=initial)
    yield event("response.in_progress", response=initial)
    for output_index, item in enumerate(final["output"]):
        if item["type"] == "function_call":
            pending = {**item, "arguments": "", "status": "in_progress"}
            yield event(
                "response.output_item.added",
                output_index=output_index,
                item=pending,
            )
            arguments = item.get("arguments", "")
            if arguments:
                yield event(
                    "response.function_call_arguments.delta",
                    item_id=item["id"],
                    output_index=output_index,
                    delta=arguments,
                )
            yield event(
                "response.function_call_arguments.done",
                item_id=item["id"],
                output_index=output_index,
                arguments=arguments,
            )
            yield event(
                "response.output_item.done",
                output_index=output_index,
                item=item,
            )
            continue
        if item["type"] != "message":
            raise RuntimeError(f"unsupported Responses output type: {item['type']!r}")
        pending = {**item, "content": [], "status": "in_progress"}
        yield event(
            "response.output_item.added", output_index=output_index, item=pending
        )
        for content_index, part in enumerate(item["content"]):
            empty_part = {**part, "text": ""}
            yield event(
                "response.content_part.added",
                item_id=item["id"],
                output_index=output_index,
                content_index=content_index,
                part=empty_part,
            )
            yield event(
                "response.output_text.delta",
                item_id=item["id"],
                output_index=output_index,
                content_index=content_index,
                delta=part["text"],
            )
            yield event(
                "response.output_text.done",
                item_id=item["id"],
                output_index=output_index,
                content_index=content_index,
                text=part["text"],
            )
            yield event(
                "response.content_part.done",
                item_id=item["id"],
                output_index=output_index,
                content_index=content_index,
                part=part,
            )
        yield event("response.output_item.done", output_index=output_index, item=item)
    yield event("response.completed", response=final)


class MetaGenBackend:
    """Submit one MetaGen request and return its completed response."""

    def __init__(
        self,
        *,
        api_key: str,
        allowed_model: str,
        submission_mode: str,
        poll_interval_sec: float,
        timeout_sec: float,
        state_dir: Path | None,
    ) -> None:
        import metagen.bento
        from metagen import (
            Dialog,
            DialogMessage,
            DialogSource,
            DialogTextContent,
            DialogToolCallRequestContentV2,
            DialogToolCallResponseContent,
            MetaGenKey,
        )

        self._metagen = metagen.bento
        self._Dialog = Dialog
        self._DialogMessage = DialogMessage
        self._DialogSource = DialogSource
        self._DialogTextContent = DialogTextContent
        self._DialogToolCallRequestContentV2 = DialogToolCallRequestContentV2
        self._DialogToolCallResponseContent = DialogToolCallResponseContent
        self._MetaGenKey = MetaGenKey
        self._api_key = api_key
        self._allowed_model = allowed_model
        if submission_mode not in _SUBMISSION_MODES:
            raise ValueError(
                f"submission_mode must be one of {sorted(_SUBMISSION_MODES)}, "
                f"received {submission_mode!r}"
            )
        self._submission_mode = submission_mode
        self._poll_interval_sec = poll_interval_sec
        self._timeout_sec = timeout_sec
        self._state_dir = state_dir
        if state_dir is not None:
            state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def model(self) -> str:
        return self._allowed_model

    def _platform(self):
        return self._metagen.create_metagen_platform(
            metagen_auth_credential=self._MetaGenKey(key=self._api_key),
            auto_rate_limit=True,
        )

    def _text_message(self, source: object, text: str) -> object:
        return self._DialogMessage(
            source=source, contents=[self._DialogTextContent(text=text)]
        )

    def _chat_dialog(self, messages: list[dict[str, Any]]):
        sources = {
            "system": self._DialogSource.SYSTEM,
            "developer": self._DialogSource.SYSTEM,
            "user": self._DialogSource.USER,
            "assistant": self._DialogSource.ASSISTANT,
        }
        converted = []
        for message in messages:
            role = str(message.get("role", ""))
            if role not in sources:
                raise ValueError(f"unsupported chat role: {role!r}")
            converted.append(
                self._text_message(
                    sources[role], _message_text(message.get("content", ""))
                )
            )
        if not converted:
            raise ValueError("messages must not be empty")
        return self._Dialog(messages=converted)

    def _responses_dialog(self, payload: dict[str, Any]):
        messages = []
        instructions = payload.get("instructions")
        if instructions:
            messages.append(
                self._text_message(
                    self._DialogSource.SYSTEM, _message_text(instructions)
                )
            )

        raw_input = payload.get("input")
        if isinstance(raw_input, str):
            raw_input = [{"type": "message", "role": "user", "content": raw_input}]
        if not isinstance(raw_input, list):
            raise TypeError("Responses input must be a string or a list")
        call_names: dict[str, str] = {}
        sources = {
            "system": self._DialogSource.SYSTEM,
            "developer": self._DialogSource.SYSTEM,
            "user": self._DialogSource.USER,
            "assistant": self._DialogSource.ASSISTANT,
        }
        for item in raw_input:
            if not isinstance(item, dict):
                raise TypeError("Responses input items must be objects")
            item_type = item.get("type", "message")
            if item_type == "message":
                role = str(item.get("role", "user"))
                if role not in sources:
                    raise ValueError(f"unsupported Responses message role: {role!r}")
                messages.append(
                    self._text_message(
                        sources[role], _message_text(item.get("content", ""))
                    )
                )
            elif item_type == "function_call":
                name = str(item.get("name", ""))
                call_id = str(item.get("call_id", ""))
                if not name or not call_id:
                    raise ValueError("function_call requires name and call_id")
                call_names[call_id] = name
                messages.append(
                    self._DialogMessage(
                        source=self._DialogSource.ASSISTANT,
                        contents=[
                            self._DialogToolCallRequestContentV2(
                                tool_call_text=json.dumps(
                                    {
                                        "id": call_id,
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": str(
                                                item.get("arguments") or "{}"
                                            ),
                                        },
                                    }
                                )
                            )
                        ],
                    )
                )
            elif item_type == "function_call_output":
                call_id = str(item.get("call_id", ""))
                if not call_id:
                    raise ValueError("function_call_output requires call_id")
                messages.append(
                    self._DialogMessage(
                        source=self._DialogSource.TOOL,
                        contents=[
                            self._DialogToolCallResponseContent(
                                text=_message_text(item.get("output", "")),
                                tool_name=call_names.get(call_id, ""),
                                tool_data=call_id,
                            )
                        ],
                    )
                )
            elif item_type == "reasoning":
                continue
            else:
                raise ValueError(f"unsupported Responses input type: {item_type!r}")
        if not messages:
            raise ValueError("Responses input must not be empty")
        return self._Dialog(messages=messages)

    @staticmethod
    def _responses_tools(payload: dict[str, Any]) -> str | None:
        raw_tools = payload.get("tools") or []
        if not isinstance(raw_tools, list):
            raise TypeError("tools must be a list")
        tools = []
        ignored_types: set[str] = set()
        for raw in raw_tools:
            if not isinstance(raw, dict):
                raise TypeError("tool definitions must be objects")
            tool_type = str(raw.get("type", ""))
            if tool_type != "function":
                ignored_types.add(tool_type or "missing")
                continue
            function = raw.get("function")
            source = function if isinstance(function, dict) else raw
            name = source.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("function tools require a name")
            tool: dict[str, Any] = {
                "type": "function",
                "name": name,
                "description": str(source.get("description") or ""),
                "parameters": source.get("parameters") or {"type": "object"},
            }
            if source.get("strict") is not None:
                tool["strict"] = bool(source["strict"])
            tools.append(tool)
        if ignored_types:
            LOG.info("ignoring unsupported Codex tool types: %s", sorted(ignored_types))
        return json.dumps(tools) if tools else None

    def _write_state(self, local_id: str, value: dict[str, Any]) -> None:
        if self._state_dir is None:
            return
        path = self._state_dir / f"{local_id}.json"
        temporary = path.with_name(path.name + ".incoming")
        temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
        os.replace(temporary, path)

    def safe_error(self, exc: Exception) -> str:
        return f"{type(exc).__name__}: {exc}".replace(self._api_key, "[REDACTED]")

    async def _run_completion(
        self,
        payload: dict[str, Any],
        *,
        dialog: object,
        tools: str | None,
        wire_api: str,
        heartbeat: Callable[[], None] | None = None,
    ) -> object:
        model = str(payload.get("model", ""))
        if model != self._allowed_model:
            raise ValueError(
                f"model must be {self._allowed_model!r}, received {model!r}"
            )
        local_id = uuid.uuid4().hex
        platform = self._platform()
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, dict):
            reasoning_effort = str(reasoning.get("effort") or "high")
        else:
            reasoning_effort = str(payload.get("reasoning_effort") or "high")
        max_tokens = int(
            payload.get("max_output_tokens")
            or payload.get("max_completion_tokens")
            or payload.get("max_tokens")
            or os.environ.get("METAGEN_MAX_OUTPUT_TOKENS", "32768")
        )
        kwargs: dict[str, Any] = {
            "request_id": local_id,
            "model": model,
            "dialog": dialog,
            "max_tokens": max(16, max_tokens),
            "reasoning_effort": reasoning_effort,
            "background": self._submission_mode == "background",
        }
        if tools is not None:
            kwargs["tools"] = tools
        if payload.get("temperature") is not None:
            kwargs["temperature"] = float(payload["temperature"])
        if payload.get("top_p") is not None:
            kwargs["top_p"] = float(payload["top_p"])

        started = time.monotonic()
        submitted_unix = time.time()
        response = await platform.dialog_completion_async(**kwargs)
        response_id_value = getattr(response, "completion_response_id", None)
        response_id = str(response_id_value) if response_id_value is not None else None
        state = _state_name(response)
        if self._submission_mode == "foreground":
            self._write_state(
                local_id,
                {
                    "local_id": local_id,
                    "wire_api": wire_api,
                    "model": model,
                    "submission_mode": self._submission_mode,
                    "state": state or "COMPLETED",
                    "submitted_unix": submitted_unix,
                    "updated_unix": time.time(),
                    "elapsed_sec": time.monotonic() - started,
                    "usage": _chat_usage(response),
                },
            )
            return response
        self._write_state(
            local_id,
            {
                "local_id": local_id,
                "wire_api": wire_api,
                "model": model,
                "submission_mode": self._submission_mode,
                "completion_response_id": response_id,
                "state": state,
                "submitted_unix": submitted_unix,
            },
        )
        while state not in _TERMINAL_STATES:
            if not response_id:
                raise RuntimeError(
                    f"MetaGen returned state {state!r} without a response id"
                )
            if time.monotonic() - started >= self._timeout_sec:
                raise TimeoutError(
                    f"MetaGen request {response_id} did not finish within "
                    f"{self._timeout_sec:g}s"
                )
            if heartbeat is not None:
                heartbeat()
            await asyncio.sleep(self._poll_interval_sec)
            response = await platform.dialog_completion_request_status_async(
                completion_response_id=response_id
            )
            state = _state_name(response)
            state_record: dict[str, Any] = {
                "local_id": local_id,
                "wire_api": wire_api,
                "model": model,
                "submission_mode": self._submission_mode,
                "completion_response_id": response_id,
                "state": state,
                "submitted_unix": submitted_unix,
                "updated_unix": time.time(),
            }
            if state in _TERMINAL_STATES:
                state_record["elapsed_sec"] = time.monotonic() - started
                state_record["usage"] = _chat_usage(response)
            self._write_state(local_id, state_record)
        if state != "SUCCEEDED":
            detail = getattr(response, "background_exception", None)
            raise RuntimeError(f"MetaGen request ended in {state}: {detail or ''}")
        return response

    async def _complete_chat_async(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._run_completion(
            payload,
            dialog=self._chat_dialog(payload.get("messages") or []),
            tools=None,
            wire_api="chat.completions",
        )
        return {
            "id": getattr(response, "request_id", None) or _new_id("chatcmpl"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self._allowed_model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": _response_text(response),
                    },
                    "finish_reason": _finish_reason(response),
                }
            ],
            "usage": _chat_usage(response),
        }

    async def _complete_responses_async(
        self,
        payload: dict[str, Any],
        response_id: str,
        heartbeat: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        response = await self._run_completion(
            payload,
            dialog=self._responses_dialog(payload),
            tools=self._responses_tools(payload),
            wire_api="responses",
            heartbeat=heartbeat,
        )
        return _responses_shell(
            payload,
            response_id,
            status="completed",
            output=_response_output_items(response),
            usage=_responses_usage(response),
        )

    def complete_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self._complete_chat_async(payload))

    def complete_responses(
        self,
        payload: dict[str, Any],
        response_id: str,
        heartbeat: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        return asyncio.run(
            self._complete_responses_async(payload, response_id, heartbeat)
        )

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.complete_chat(payload)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        backend: MetaGenBackend,
        max_concurrency: int,
    ) -> None:
        super().__init__(address, _Handler)
        self.backend = backend
        self.capacity = threading.BoundedSemaphore(max_concurrency)


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def _json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _sse(self, event: dict[str, Any]) -> None:
        type_ = event["type"]
        encoded = f"event: {type_}\ndata: {json.dumps(event)}\n\n".encode()
        self.wfile.write(encoded)
        self.wfile.flush()

    def _heartbeat(self) -> None:
        try:
            self.wfile.write(b": metagen-poll\n\n")
            self.wfile.flush()
        except OSError:
            pass

    def _responses(self, payload: dict[str, Any]) -> None:
        response_id = _new_id("resp")
        initial = _responses_shell(
            payload,
            response_id,
            status="in_progress",
            output=[],
            usage=None,
        )
        if not payload.get("stream", False):
            final = self.server.backend.complete_responses(payload, response_id)
            self._json(HTTPStatus.OK, final)
            return

        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        events = _responses_events(initial, initial)
        self._sse(next(events))
        self._sse(next(events))
        try:
            final = self.server.backend.complete_responses(
                payload, response_id, self._heartbeat
            )
            for event in list(_responses_events(initial, final))[2:]:
                self._sse(event)
        except Exception as exc:  # noqa: BLE001 - stream a provider failure
            safe_error = self.server.backend.safe_error(exc)
            LOG.error("MetaGen Responses request failed: %s", safe_error)
            failed = _responses_shell(
                payload,
                response_id,
                status="failed",
                output=[],
                usage=None,
                error={"type": "metagen_error", "message": safe_error[:2000]},
            )
            self._sse({"type": "response.failed", "response": failed})
        self.close_connection = True

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "model": self.server.backend.model,
                    "wire_apis": ["chat.completions", "responses"],
                },
            )
        else:
            self._json(
                HTTPStatus.NOT_FOUND,
                {"error": {"message": "not found", "type": "invalid_request_error"}},
            )

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/v1/analytics/codex/turn-costs":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if path not in {"/v1/chat/completions", "/v1/responses"}:
            self._json(
                HTTPStatus.NOT_FOUND,
                {"error": {"message": "not found", "type": "invalid_request_error"}},
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > _MAX_REQUEST_BYTES:
                raise ValueError("invalid request body size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise TypeError("request body must be a JSON object")
            with self.server.capacity:
                if path == "/v1/responses":
                    self._responses(payload)
                else:
                    response = self.server.backend.complete_chat(payload)
                    self._json(HTTPStatus.OK, response)
        except (TypeError, ValueError) as exc:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"message": str(exc), "type": "invalid_request_error"}},
            )
        except Exception as exc:  # noqa: BLE001 - convert provider failures to HTTP
            safe_error = self.server.backend.safe_error(exc)
            LOG.error("MetaGen request failed: %s", safe_error)
            self._json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "error": {
                        "message": safe_error[:2000],
                        "type": "metagen_error",
                    }
                },
            )

    def log_message(self, format_: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), format_ % args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument(
        "--model",
        default=os.environ.get("SYNTH_MODEL", "gpt-5-6-sol-genai-background-mode"),
    )
    parser.add_argument(
        "--submission-mode",
        choices=sorted(_SUBMISSION_MODES),
        default=os.environ.get("METAGEN_SUBMISSION_MODE", "background"),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("METAGEN_POLL_INTERVAL_SEC", "2")),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("METAGEN_REQUEST_TIMEOUT_SEC", "7200")),
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=int(os.environ.get("METAGEN_PROXY_MAX_CONCURRENCY", "20")),
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=(
            Path(os.environ["METAGEN_PROXY_STATE_DIR"])
            if os.environ.get("METAGEN_PROXY_STATE_DIR")
            else None
        ),
    )
    args = parser.parse_args()

    key = os.environ.get("METAGEN_API_KEY", "").strip()
    if not key:
        raise SystemExit("METAGEN_API_KEY is required")
    if args.poll_interval <= 0 or args.timeout <= 0 or args.max_concurrency <= 0:
        raise SystemExit("poll interval, timeout, and max concurrency must be positive")

    logging.basicConfig(
        level=os.environ.get("METAGEN_PROXY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    backend = MetaGenBackend(
        api_key=key,
        allowed_model=args.model,
        submission_mode=args.submission_mode,
        poll_interval_sec=args.poll_interval,
        timeout_sec=args.timeout,
        state_dir=args.state_dir,
    )
    server = _Server((args.host, args.port), backend, args.max_concurrency)

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    LOG.info("listening on http://%s:%d/v1", args.host, args.port)
    server.serve_forever(poll_interval=0.5)
    server.server_close()


if __name__ == "__main__":
    main()
