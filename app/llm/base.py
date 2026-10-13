"""The LLM interface shared by all providers, plus errors and retry policy.

Providers translate SDK-specific failures into the exceptions below, so callers
(CLI, UI, answer generator) handle one set of errors. Logging is limited to
metadata: provider, model, token counts and latency. Prompts, retrieved code,
answers and keys are never logged.
"""

import logging
import time
from collections.abc import Callable
from typing import Protocol, TypeVar

from pydantic import BaseModel

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


class LLMProvider(Protocol):
    """Anything that turns a system prompt and a user message into text."""

    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def generate(self, system: str, user: str) -> LLMResponse: ...


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


def log_response(response: LLMResponse) -> None:
    """Log metadata only, never content."""
    logger.info(
        "LLM call provider=%s model=%s input_tokens=%s output_tokens=%s latency_ms=%.0f stop=%s",
        response.provider, response.model, response.input_tokens, response.output_tokens,
        response.latency_ms, response.stop_reason,
    )
