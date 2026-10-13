"""A deterministic LLM for tests. Never calls a network."""

from collections.abc import Callable

from app.llm.base import LLMResponse

Responder = Callable[[str, str], str]

DEFAULT_RESPONSE = "From the project:\n- The answer is in the first source [S1]."


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
    ) -> None:
        self._response = response
        self._error = error
        self._model = model
        self.calls: list[tuple[str, str]] = []

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
