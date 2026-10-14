"""The LLM interface shared by all providers, plus errors and retry policy.

Providers translate SDK-specific failures into the exceptions below, so callers
(CLI, UI, answer generator) handle one set of errors. Logging is limited to
metadata: provider, model, token counts and latency. Prompts, retrieved code,
answers and keys are never logged.
"""

import logging
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRY_BACKOFF_SECONDS = 2.0


class LLMResponse(BaseModel):
    """One completed generation."""

    text: str
    model: str
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float
    stop_reason: str | None = None


# ---- tool calling ------------------------------------------------------------


class ToolSpec(BaseModel):
    """A tool the model may call: name, description and JSON Schema for its arguments."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    """A tool call requested by the model, with parsed arguments."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    parse_error: str | None = None  # set when the model sent arguments that aren't a JSON object


class ChatMessage(BaseModel):
    """One message in a provider-neutral conversation.

    ``provider_payload`` keeps an assistant message exactly as the provider
    returned it (e.g. thinking or reasoning blocks), so it can be sent back
    unchanged on the next request, as the providers require during tool use.
    """

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # role="tool": which call this answers
    is_error: bool = False  # role="tool": the tool failed
    provider_payload: Any = Field(default=None, exclude=True)


class ChatResponse(BaseModel):
    """A normalized chat turn: text and/or tool calls, plus usage."""

    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float
    stop_reason: str | None = None
    message: ChatMessage  # the assistant message to append to the conversation


ToolChoice = Literal["auto", "none"]


class LLMProvider(Protocol):
    """Anything that turns a system prompt and a user message into text."""

    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def generate(self, system: str, user: str) -> LLMResponse: ...


class ToolCallingLLM(LLMProvider, Protocol):
    """A provider that also supports multi-turn chat with tools."""

    def chat(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec], tool_choice: ToolChoice = "auto"
    ) -> ChatResponse: ...


# ---- errors ---------------------------------------------------------------


class LLMError(Exception):
    """Base class for all LLM failures. Messages never contain secrets."""


class LLMConfigError(LLMError):
    """Missing or invalid configuration (model name, API key, provider)."""


class LLMAuthError(LLMError):
    """The provider rejected the credentials."""


class LLMBadRequestError(LLMError):
    """The provider rejected the request (e.g. unknown model, invalid parameter)."""


class LLMEmptyResponseError(LLMError):
    """The provider returned no usable text."""


class LLMRefusalError(LLMError):
    """The model declined to answer."""


class LLMToolsUnsupportedError(LLMError):
    """The configured model can't call tools."""


class TransientLLMError(LLMError):
    """A failure that may succeed on retry."""


class LLMTimeoutError(TransientLLMError):
    """The request timed out."""


class LLMRateLimitError(TransientLLMError):
    """The provider is rate limiting requests."""


class LLMConnectionError(TransientLLMError):
    """The provider could not be reached."""


class LLMServerError(TransientLLMError):
    """The provider had an internal error or is overloaded."""


# ---- helpers --------------------------------------------------------------


def with_retry(call: Callable[[], T], retries: int = 1, backoff_seconds: float = RETRY_BACKOFF_SECONDS) -> T:
    """Run ``call``, retrying transient errors ``retries`` times with exponential backoff."""
    for attempt in range(retries + 1):
        try:
            return call()
        except TransientLLMError as exc:
            if attempt == retries:
                raise
            delay = backoff_seconds * 2**attempt
            logger.warning("Transient LLM error (%s); retrying in %.1fs", type(exc).__name__, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def timed(call: Callable[[], T]) -> tuple[T, float]:
    """Return ``call()``'s result and its duration in milliseconds."""
    started = time.perf_counter()
    result = call()
    return result, (time.perf_counter() - started) * 1000


def log_response(response: LLMResponse | ChatResponse) -> None:
    """Log metadata only, never content."""
    calls = len(response.tool_calls) if isinstance(response, ChatResponse) else 0
    logger.info(
        "LLM call provider=%s model=%s input_tokens=%s output_tokens=%s latency_ms=%.0f stop=%s tool_calls=%d",
        response.provider, response.model, response.input_tokens, response.output_tokens,
        response.latency_ms, response.stop_reason, calls,
    )
