"""Tool calling in the LLM providers (SDK clients mocked, no network)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx2
import openai
import pytest
from pydantic import SecretStr

from app.llm import base
from app.llm.anthropic_provider import AnthropicProvider, to_anthropic_messages
from app.llm.base import ChatMessage, LLMToolsUnsupportedError, ToolCall, ToolSpec
from app.llm.fake_provider import FakeLLMProvider, FakeToolCalls
from app.llm.openai_compatible import OpenAICompatibleProvider, to_openai_messages

TOOLS = [ToolSpec(name="search_project", description="Search.", parameters={
    "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base.time, "sleep", lambda _: None)


# ---- Anthropic ---------------------------------------------------------------------


def anthropic_reply(*blocks: SimpleNamespace, stop: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), model="claude-test", stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=50, output_tokens=20))


def test_anthropic_chat_parses_tool_calls_and_keeps_raw_content() -> None:
    blocks = (SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="Let me search."),
              SimpleNamespace(type="tool_use", id="toolu_1", name="search_project", input={"query": "auth"}))
    client = MagicMock()
    client.messages.create.return_value = anthropic_reply(*blocks)
    provider = AnthropicProvider("claude-test", SecretStr("k"), client=client)

    response = provider.chat("sys", [ChatMessage(role="user", content="q")], TOOLS, tool_choice="auto")
    assert response.text == "Let me search."
    assert response.tool_calls == [ToolCall(id="toolu_1", name="search_project", arguments={"query": "auth"})]
    assert response.message.provider_payload == list(blocks)  # thinking block preserved for the next turn
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tools"] == [{"name": "search_project", "description": "Search.", "input_schema": TOOLS[0].parameters}]
    assert kwargs["tool_choice"] == {"type": "auto"} and kwargs["system"] == "sys"
    assert "temperature" not in kwargs


def test_anthropic_final_turn_uses_tool_choice_none_and_keeps_tools() -> None:
    client = MagicMock()
    client.messages.create.return_value = anthropic_reply(SimpleNamespace(type="text", text="Done."), stop="end_turn")
    provider = AnthropicProvider("claude-test", SecretStr("k"), client=client)
    provider.chat("sys", [ChatMessage(role="user", content="q")], TOOLS, tool_choice="none")
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "none"} and kwargs["tools"]


def test_anthropic_message_conversion_groups_tool_results() -> None:
    call = ToolCall(id="t1", name="search_project", arguments={"query": "x"})
    messages = [
        ChatMessage(role="user", content="q"),
        ChatMessage(role="assistant", content="", tool_calls=[call, call.model_copy(update={"id": "t2"})]),
        ChatMessage(role="tool", tool_call_id="t1", content='{"ok": true}'),
        ChatMessage(role="tool", tool_call_id="t2", content='{"ok": false}', is_error=True),
        ChatMessage(role="user", content="final note"),
    ]
    out = to_anthropic_messages(messages)
    assert [m["role"] for m in out] == ["user", "assistant", "user", "user"]
    assert [b["type"] for b in out[1]["content"]] == ["tool_use", "tool_use"]
    results = out[2]["content"]
    assert [(r["tool_use_id"], r["is_error"]) for r in results] == [("t1", False), ("t2", True)]


# ---- OpenAI-compatible --------------------------------------------------------------


def openai_reply(content: str | None, tool_calls: list | None = None, extra: dict | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, refusal=None, tool_calls=tool_calls, model_extra=extra or {})
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if tool_calls else "stop")],
                           model="m", usage=SimpleNamespace(prompt_tokens=40, completion_tokens=8))


def openai_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments))


def test_openai_chat_parses_calls_and_passes_reasoning_back() -> None:
    reasoning = [{"type": "reasoning.encrypted", "data": "opaque", "id": "r1", "format": "x"}]
    client = MagicMock()
    client.chat.completions.create.return_value = openai_reply(
        None, [openai_call("c1", "search_project", '{"query": "auth"}'), openai_call("c2", "search_project", "not json"),
               openai_call("c3", "search_project", "[1, 2]")], extra={"reasoning_details": reasoning})
    provider = OpenAICompatibleProvider("m", SecretStr("k"), base_url="https://openrouter.ai/api/v1", client=client)

    response = provider.chat("sys", [ChatMessage(role="user", content="q")], TOOLS)
    assert response.tool_calls[0].arguments == {"query": "auth"}
    assert response.tool_calls[1].parse_error == "arguments are not valid JSON"
    assert response.tool_calls[2].parse_error == "arguments must be a JSON object"
    payload = response.message.provider_payload
    assert payload["reasoning_details"] == reasoning  # unmodified, for the next request
    assert payload["tool_calls"][0]["function"]["arguments"] == '{"query": "auth"}'

    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert kwargs["tools"][0]["type"] == "function" and kwargs["tools"][0]["function"]["name"] == "search_project"
    assert kwargs["tool_choice"] == "auto" and kwargs["max_tokens"] == 1024


def test_openai_message_conversion() -> None:
    call = ToolCall(id="c1", name="search_project", arguments={"query": "x"})
    out = to_openai_messages([
        ChatMessage(role="user", content="q"),
        ChatMessage(role="assistant", content="", tool_calls=[call]),
        ChatMessage(role="tool", tool_call_id="c1", content="{}"),
    ])
    assert out[1]["tool_calls"][0]["function"] == {"name": "search_project", "arguments": '{"query": "x"}'}
    assert out[2] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}


def test_model_without_tool_support_is_a_clear_error() -> None:
    request = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    error = openai.NotFoundError("No endpoints found that support tool use.",
                                 response=httpx2.Response(404, request=request), body=None)
    client = MagicMock()
    client.chat.completions.create.side_effect = error
    provider = OpenAICompatibleProvider("text-only-model", SecretStr("k"), base_url="https://x/v1", client=client)
    with pytest.raises(LLMToolsUnsupportedError, match="does not support tool calling"):
        provider.chat("sys", [ChatMessage(role="user", content="q")], TOOLS)


# ---- fake ------------------------------------------------------------------------------


def test_fake_script_plays_in_order_and_respects_tool_choice_none() -> None:
    fake = FakeLLMProvider(script=[FakeToolCalls([("search_project", {"query": "a"})]),
                                   FakeToolCalls([("search_project", {"query": "b"})]), "final"])
    first = fake.chat("s", [], TOOLS)
    assert first.tool_calls[0].arguments == {"query": "a"} and first.tool_calls[0].id == "call_1"
    final = fake.chat("s", [], TOOLS, tool_choice="none")  # skips the second tool step
    assert final.text == "final" and not final.tool_calls
    assert [c.tool_choice for c in fake.chat_calls] == ["auto", "none"]
    assert "No further steps" in fake.chat("s", [], TOOLS).text  # exhausted script
