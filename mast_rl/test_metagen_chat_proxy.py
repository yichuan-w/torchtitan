from __future__ import annotations

import asyncio
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("metagen_chat_proxy.py")
SPEC = importlib.util.spec_from_file_location("metagen_chat_proxy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)


@dataclass
class _State:
    name: str


@dataclass
class _Reason:
    name: str


@dataclass
class _Content:
    text: str | None = None
    answer: str | None = None
    arguments: str | None = None
    call_id: str | None = None
    name: str | None = None
    id: str | None = None
    tool_call_text: str | None = None


@dataclass
class _Message:
    contents: list[object]
    source: object | None = None


@dataclass
class _Dialog:
    messages: list[_Message]


@dataclass
class _Choice:
    dialog: _Dialog
    finish_reason: _Reason


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    num_reasoning_tokens: int
    num_cached_prompt_tokens: int


@dataclass
class _Response:
    choices: list[_Choice]
    processing_state: _State
    request_id: str = "request"
    completion_response_id: str = "background"
    usage: _Usage | None = None
    background_exception: Exception | None = None


@dataclass
class _InputText:
    text: str


@dataclass
class _InputFunctionCall:
    tool_call_text: str


@dataclass
class _InputToolOutput:
    text: str
    tool_name: str
    tool_data: str


@dataclass
class _InputMessage:
    source: str
    contents: list[object] = field(default_factory=list)


class _InputDialog:
    def __init__(self, *, messages: list[_InputMessage]) -> None:
        self.messages = messages


class _Sources:
    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


def _backend() -> proxy.MetaGenBackend:
    backend = object.__new__(proxy.MetaGenBackend)
    backend._Dialog = _InputDialog
    backend._DialogMessage = _InputMessage
    backend._DialogSource = _Sources
    backend._DialogTextContent = _InputText
    backend._DialogToolCallRequestContentV2 = _InputFunctionCall
    backend._DialogToolCallResponseContent = _InputToolOutput
    backend._allowed_model = "background-model"
    backend._submission_mode = "background"
    backend._poll_interval_sec = 0.001
    backend._timeout_sec = 1.0
    backend._state_dir = None
    backend._api_key = "secret"
    return backend


def test_platform_enables_sdk_rate_limit_handling() -> None:
    backend = _backend()
    seen = {}

    class _MetaGen:
        @staticmethod
        def create_metagen_platform(**kwargs):
            seen.update(kwargs)
            return "platform"

    backend._metagen = _MetaGen
    backend._MetaGenKey = lambda *, key: ("key", key)
    assert backend._platform() == "platform"
    assert seen == {
        "metagen_auth_credential": ("key", "secret"),
        "auto_rate_limit": True,
    }


def _completed(contents: list[object]) -> _Response:
    return _Response(
        choices=[
            _Choice(
                dialog=_Dialog(messages=[_Message(contents)]),
                finish_reason=_Reason("STOP"),
            )
        ],
        processing_state=_State("SUCCEEDED"),
        usage=_Usage(10, 7, 17, 4, 3),
    )


def test_message_text_accepts_openai_text_parts() -> None:
    assert (
        proxy._message_text(
            [
                {"type": "text", "text": "one"},
                {"type": "input_text", "text": "two"},
            ]
        )
        == "one\ntwo"
    )


def test_response_conversion_prefers_answer_and_maps_usage() -> None:
    response = _Response(
        choices=[
            _Choice(
                dialog=_Dialog(
                    messages=[
                        _Message([_Content(text="ignored")]),
                        _Message([_Content(answer="done")]),
                    ]
                ),
                finish_reason=_Reason("MAX_OUTPUT_TOKENS"),
            )
        ],
        processing_state=_State("SUCCEEDED"),
        usage=_Usage(10, 7, 17, 4, 3),
    )
    assert proxy._response_text(response) == "done"
    assert proxy._finish_reason(response) == "length"
    assert proxy._chat_usage(response) == {
        "prompt_tokens": 10,
        "completion_tokens": 7,
        "total_tokens": 17,
        "completion_tokens_details": {"reasoning_tokens": 4},
        "prompt_tokens_details": {"cached_tokens": 3},
    }
    assert proxy._responses_usage(response)["total_tokens"] == 17


def test_structured_and_opaque_tool_calls_become_response_items() -> None:
    structured = _Content(
        arguments='{"cmd":"pwd"}',
        call_id="call_1",
        name="exec_command",
        id="fc_1",
    )
    opaque = _Content(
        tool_call_text=json.dumps(
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "write_stdin",
                    "arguments": '{"session_id":7,"chars":""}',
                },
            }
        )
    )
    items = proxy._response_output_items(_completed([structured, opaque]))
    assert [(item["name"], item["call_id"]) for item in items] == [
        ("exec_command", "call_1"),
        ("write_stdin", "call_2"),
    ]


def test_responses_dialog_preserves_calls_and_tool_outputs() -> None:
    backend = _backend()
    dialog = backend._responses_dialog(
        {
            "instructions": "system policy",
            "input": [
                {"type": "message", "role": "user", "content": "inspect"},
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "exec_command",
                    "arguments": '{"cmd":"pwd"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "/work",
                },
                {"type": "reasoning", "id": "rs_1", "summary": []},
            ],
        }
    )
    assert [message.source for message in dialog.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    call = dialog.messages[2].contents[0]
    output = dialog.messages[3].contents[0]
    assert json.loads(call.tool_call_text) == {
        "id": "call_1",
        "type": "function",
        "function": {"name": "exec_command", "arguments": '{"cmd":"pwd"}'},
    }
    assert output.tool_name == "exec_command" and output.tool_data == "call_1"


def test_responses_tools_are_flat_and_non_function_tools_are_ignored() -> None:
    tools = proxy.MetaGenBackend._responses_tools(
        {
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "description": "run a command",
                    "parameters": {"type": "object"},
                },
                {"type": "namespace", "name": "multi_agent_v1"},
            ]
        }
    )
    assert json.loads(tools) == [
        {
            "type": "function",
            "name": "exec_command",
            "description": "run a command",
            "parameters": {"type": "object"},
        }
    ]


def test_response_events_include_complete_function_lifecycle() -> None:
    payload = {"model": "model", "tools": [], "stream": True}
    initial = proxy._responses_shell(
        payload, "resp_1", status="in_progress", output=[], usage=None
    )
    final = proxy._responses_shell(
        payload,
        "resp_1",
        status="completed",
        output=[
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "exec_command",
                "arguments": '{"cmd":"pwd"}',
                "status": "completed",
            }
        ],
        usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    )
    event_types = [event["type"] for event in proxy._responses_events(initial, final)]
    assert event_types == [
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response.completed",
    ]


def test_terminal_state_names_are_explicit() -> None:
    assert proxy._state_name(_Response([], _State("IN_PROGRESS"))) == "IN_PROGRESS"
    assert proxy._state_name(_Response([], _State("SUCCEEDED"))) == "SUCCEEDED"


def test_background_chat_request_is_submitted_and_polled(tmp_path: Path) -> None:
    completed = _completed([_Content(answer="done")])

    class _Platform:
        def __init__(self) -> None:
            self.submitted: dict | None = None
            self.polled: str | None = None

        async def dialog_completion_async(self, **kwargs):
            self.submitted = kwargs
            return _Response([], _State("IN_PROGRESS"))

        async def dialog_completion_request_status_async(
            self, *, completion_response_id: str
        ):
            self.polled = completion_response_id
            return completed

    platform = _Platform()
    backend = _backend()
    backend._state_dir = tmp_path
    backend._platform = lambda: platform

    result = asyncio.run(
        backend._complete_chat_async(
            {
                "model": "background-model",
                "messages": [{"role": "user", "content": "hello"}],
                "max_completion_tokens": 32,
                "reasoning_effort": "high",
            }
        )
    )

    assert platform.submitted is not None
    assert platform.submitted["background"] is True
    assert platform.polled == "background"
    assert result["choices"][0]["message"]["content"] == "done"
    assert result["usage"]["total_tokens"] == 17
    record = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert record["state"] == "SUCCEEDED"
    assert record["wire_api"] == "chat.completions"


def test_background_responses_request_forwards_flat_tools() -> None:
    completed = _completed(
        [
            _Content(
                arguments='{"cmd":"pwd"}',
                call_id="call_1",
                name="exec_command",
                id="fc_1",
            )
        ]
    )

    class _Platform:
        submitted: dict | None = None

        async def dialog_completion_async(self, **kwargs):
            self.submitted = kwargs
            return completed

    platform = _Platform()
    backend = _backend()
    backend._platform = lambda: platform
    result = asyncio.run(
        backend._complete_responses_async(
            {
                "model": "background-model",
                "input": [{"role": "user", "content": "inspect"}],
                "tools": [
                    {
                        "type": "function",
                        "name": "exec_command",
                        "description": "run",
                        "parameters": {"type": "object"},
                    }
                ],
                "reasoning": {"effort": "medium"},
            },
            "resp_1",
        )
    )
    assert platform.submitted is not None
    assert platform.submitted["background"] is True
    assert platform.submitted["reasoning_effort"] == "medium"
    assert json.loads(platform.submitted["tools"])[0]["name"] == "exec_command"
    assert result["output"][0]["call_id"] == "call_1"


def test_foreground_responses_request_returns_without_polling(
    tmp_path: Path,
) -> None:
    completed = _completed(
        [
            _Content(
                arguments='{"cmd":"pwd"}',
                call_id="call_1",
                name="exec_command",
                id="fc_1",
            )
        ]
    )

    class _Platform:
        submitted: dict | None = None

        async def dialog_completion_async(self, **kwargs):
            self.submitted = kwargs
            return completed

        async def dialog_completion_request_status_async(self, **kwargs):
            raise AssertionError(f"foreground request was polled: {kwargs}")

    platform = _Platform()
    backend = _backend()
    backend._submission_mode = "foreground"
    backend._state_dir = tmp_path
    backend._platform = lambda: platform
    result = asyncio.run(
        backend._complete_responses_async(
            {
                "model": "background-model",
                "input": [{"role": "user", "content": "inspect"}],
                "tools": [
                    {
                        "type": "function",
                        "name": "exec_command",
                        "description": "run",
                        "parameters": {"type": "object"},
                    }
                ],
            },
            "resp_1",
        )
    )

    assert platform.submitted is not None
    assert platform.submitted["background"] is False
    assert result["output"][0]["call_id"] == "call_1"
    record = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert record["state"] == "SUCCEEDED"
    assert record["submission_mode"] == "foreground"


def test_safe_error_redacts_key() -> None:
    backend = _backend()
    assert "secret" not in backend.safe_error(RuntimeError("bad secret"))
