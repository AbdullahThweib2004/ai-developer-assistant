"""Claude via the official Anthropic Python SDK (Messages API)."""

import anthropic
from pydantic import SecretStr

from app.llm.base import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConnectionError,
    LLMEmptyResponseError,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponse,
    LLMServerError,
    LLMTimeoutError,
    log_response,
    timed,
    with_retry,
)


class AnthropicProvider:
    """Generates text with a Claude model.

    ``temperature`` is not sent: current Claude models reject sampling
    parameters. The SDK's own retries are disabled so the retry policy in
    :func:`app.llm.base.with_retry` applies uniformly to every provider.
    """

    def __init__(
        self,
        model: str,
        api_key: SecretStr,
        max_output_tokens: int = 1024,
        timeout_seconds: float = 60,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._model = model
        self.max_output_tokens = max_output_tokens
        self._client = client or anthropic.Anthropic(
            api_key=api_key.get_secret_value(), timeout=timeout_seconds, max_retries=0
        )

    @property
    def provider(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def generate(self, system: str, user: str) -> LLMResponse:
        response = with_retry(lambda: self._call(system, user))
        log_response(response)
        return response

    def _call(self, system: str, user: str) -> LLMResponse:
        try:
            message, latency_ms = timed(lambda: self._client.messages.create(
                model=self._model,
                max_tokens=self.max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            ))
        except anthropic.APIError as exc:
            raise map_anthropic_error(exc) from None

        if message.stop_reason == "refusal":
            raise LLMRefusalError("The model declined to answer this question.")
        # Content can include thinking blocks; only text blocks form the answer.
        text = "".join(block.text for block in message.content if block.type == "text").strip()
        if not text:
            hint = " Increase LLM_MAX_OUTPUT_TOKENS." if message.stop_reason == "max_tokens" else ""
            raise LLMEmptyResponseError(f"The model returned no text (stop_reason={message.stop_reason}).{hint}")
        return LLMResponse(
            text=text,
            model=message.model,
            provider=self.provider,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            latency_ms=round(latency_ms, 1),
            stop_reason=message.stop_reason,
        )


def map_anthropic_error(exc: anthropic.APIError) -> LLMError:
    """Translate an SDK exception into our error types (no SDK message text, no secrets)."""
    if isinstance(exc, anthropic.APITimeoutError):  # subclass of APIConnectionError: check first
        return LLMTimeoutError("Anthropic request timed out. Try again or raise LLM_TIMEOUT_SECONDS.")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMConnectionError("Could not reach the Anthropic API. Check your network connection.")
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return LLMAuthError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY.")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMRateLimitError("Anthropic rate limit reached. Wait a moment and try again.")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMBadRequestError("Anthropic did not recognize the model. Check LLM_MODEL.")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return LLMServerError(f"Anthropic server error (HTTP {exc.status_code}). Try again later.")
        return LLMBadRequestError(f"Anthropic rejected the request (HTTP {exc.status_code}).")
    return LLMError(f"Anthropic request failed ({type(exc).__name__}).")
