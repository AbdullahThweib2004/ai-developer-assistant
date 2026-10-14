"""A deterministic LLM for tests. Never calls a network."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import ChatMessage, ChatResponse, LLMResponse, ToolCall, ToolChoice, ToolSpec

Responder = Callable[[str, str], str]

DEFAULT_RESPONSE = "From the project:\n- The answer is in the first source [S1]."
DEFAULT_FINAL = "## Summary\nNo further steps were scripted."


@dataclass
class FakeToolCalls:
    """A scripted assistant turn that requests tool calls: [(name, arguments), ...]."""

    calls: list[tuple[str, dict[str, Any]]]
    text: str = ""


@dataclass
class ChatCall:
    """What the fake model received on one chat turn (for assertions in tests)."""

    system: str
    messages: list[ChatMessage]
    tool_names: list[str]
    tool_choice: ToolChoice
    tools: list[ToolSpec] = field(default_factory=list)


class FakeLLMProvider:
    """Returns canned text and records every call.

    ``response`` may be a fixed string, a list of strings (returned in order,
    the last one repeated), or a function ``(system, user) -> text``.
    ``error`` (an exception) is raised instead, for error-path tests.
    """

    def __init__(
        self,
        response: str | list[str] | Responder = DEFAULT_RESPONSE,
        error: Exception | None = None,
        model: str = "fake-llm",
        script: list[str | FakeToolCalls] | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self._model = model
        self.calls: list[tuple[str, str]] = []
        # chat(): scripted turns, consumed in order; a str is a final answer.
        self.script: list[str | FakeToolCalls] = list(script or [])
        self.chat_calls: list[ChatCall] = []
        self._call_counter = 0

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return self._model

    def generate(self, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        if self._error is not None:
            raise self._error
        if callable(self._response):
            text = self._response(system, user)
        elif isinstance(self._response, list):
            text = self._response[min(len(self.calls), len(self._response)) - 1]
        else:
            text = self._response
        return LLMResponse(
            text=text,
            model=self._model,
            provider=self.provider,
            input_tokens=len(system + user) // 4,
            output_tokens=len(text) // 4,
            latency_ms=0.0,
            stop_reason="end_turn",
        )

    def chat(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec], tool_choice: ToolChoice = "auto"
    ) -> ChatResponse:
        """Play the next scripted turn. With tool_choice="none", tool-call steps are skipped
        until a final answer is found (as a compliant model would)."""
        self.chat_calls.append(ChatCall(system=system, messages=[m.model_copy() for m in messages],
                                        tool_names=[t.name for t in tools], tool_choice=tool_choice, tools=tools))
        if self._error is not None:
            raise self._error
        step: str | FakeToolCalls = DEFAULT_FINAL
        while self.script:
            candidate = self.script.pop(0)
            if isinstance(candidate, FakeToolCalls) and tool_choice == "none":
                continue
            step = candidate
            break
        if isinstance(step, FakeToolCalls):
            calls = []
            for name, args in step.calls:
                self._call_counter += 1
                calls.append(ToolCall(id=f"call_{self._call_counter}", name=name, arguments=args))
            text = step.text
        else:
            calls, text = [], step
        return ChatResponse(
            text=text, tool_calls=calls, model=self._model, provider=self.provider,
            input_tokens=sum(len(m.content) for m in messages) // 4 + len(system) // 4,
            output_tokens=len(text) // 4 + 10 * len(calls), latency_ms=0.0,
            stop_reason="tool_use" if calls else "end_turn",
            message=ChatMessage(role="assistant", content=text, tool_calls=calls),
        )
