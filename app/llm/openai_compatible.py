"""Any OpenAI-compatible Chat Completions API: OpenAI, Groq, OpenRouter, Ollama, ...

Chat Completions is used (not the Responses API) because it is the API that
third-party OpenAI-compatible servers implement.
"""

import json
from typing import Any

import openai
from pydantic import SecretStr

from app.llm.base import (
    ChatMessage,
    ChatResponse,
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
    LLMToolsUnsupportedError,
    ToolCall,
    ToolChoice,
    ToolSpec,
    log_response,
    timed,
    with_retry,
)

# Local servers such as Ollama ignore the key, but the SDK requires one.
NO_KEY_PLACEHOLDER = "not-needed"
# Reasoning fields some gateways (e.g. OpenRouter) return on assistant messages and
# ask to have sent back unmodified during tool use.
PASSTHROUGH_FIELDS = ("reasoning", "reasoning_details")


class OpenAICompatibleProvider:
    """Generates text through an OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(
        self,
        model: str,
        api_key: SecretStr | None,
        base_url: str | None = None,
        max_output_tokens: int = 1024,
        temperature: float | None = 0.1,
        timeout_seconds: float = 60,
        client: openai.OpenAI | None = None,
    ) -> None:
        self._model = model
        self.base_url = base_url
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        key = api_key.get_secret_value() if api_key else NO_KEY_PLACEHOLDER
        self._client = client or openai.OpenAI(
            api_key=key, base_url=base_url, timeout=timeout_seconds, max_retries=0
        )

    @property
    def provider(self) -> str:
        return "openai_compatible"

    @property
    def model(self) -> str:
        return self._model

    def generate(self, system: str, user: str) -> LLMResponse:
        response = with_retry(lambda: self._call(system, user))
        log_response(response)
        return response

    def _request_params(self, system: str, user: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # OpenAI deprecated max_tokens in favor of max_completion_tokens;
        # other compatible servers generally only understand max_tokens.
        token_param = "max_tokens" if self.base_url else "max_completion_tokens"
        params[token_param] = self.max_output_tokens
        if self.temperature is not None:
            params["temperature"] = self.temperature
        return params

    def _call(self, system: str, user: str) -> LLMResponse:
        try:
            completion, latency_ms = timed(
                lambda: self._client.chat.completions.create(**self._request_params(system, user))
            )
        except openai.APIError as exc:
            raise map_openai_error(exc) from None

        if not completion.choices:
            raise LLMEmptyResponseError("The model returned no choices.")
        choice = completion.choices[0]
        if getattr(choice.message, "refusal", None) or choice.finish_reason == "content_filter":
            raise LLMRefusalError("The model declined to answer this question.")
        text = (choice.message.content or "").strip()
        if not text:
            hint = " Increase LLM_MAX_OUTPUT_TOKENS." if choice.finish_reason == "length" else ""
            raise LLMEmptyResponseError(f"The model returned no text (finish_reason={choice.finish_reason}).{hint}")
        usage = completion.usage
        return LLMResponse(
            text=text,
            model=completion.model or self._model,
            provider=self.provider,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            latency_ms=round(latency_ms, 1),
            stop_reason=choice.finish_reason,
        )


    # ---- chat with tools ---------------------------------------------------------

    def chat(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec], tool_choice: ToolChoice = "auto"
    ) -> ChatResponse:
        response = with_retry(lambda: self._chat_call(system, messages, tools, tool_choice))
        log_response(response)
        return response

    def _chat_call(
        self, system: str, messages: list[ChatMessage], tools: list[ToolSpec], tool_choice: ToolChoice
    ) -> ChatResponse:
        params = self._request_params(system, "")
        params["messages"] = [{"role": "system", "content": system}, *to_openai_messages(messages)]
        if tools:  # must be sent on every request of a tool conversation
            params["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
            params["tool_choice"] = tool_choice
        try:
            completion, latency_ms = timed(lambda: self._client.chat.completions.create(**params))
        except openai.APIError as exc:
            if tools and _mentions_tool_support(exc):
                raise LLMToolsUnsupportedError(
                    f"The model '{self._model}' does not support tool calling. Choose a tool-capable model for "
                    "LLM_MODEL (on OpenRouter, filter models by supported parameter 'tools')."
                ) from None
            raise map_openai_error(exc) from None

        if not completion.choices:
            raise LLMEmptyResponseError("The model returned no choices.")
        choice = completion.choices[0]
        message = choice.message
        if getattr(message, "refusal", None) or choice.finish_reason == "content_filter":
            raise LLMRefusalError("The model declined to answer this question.")
        text = (message.content or "").strip()
        calls = [_parse_tool_call(c) for c in (message.tool_calls or [])]
        if not text and not calls:
            hint = " Increase LLM_MAX_OUTPUT_TOKENS." if choice.finish_reason == "length" else ""
            raise LLMEmptyResponseError(f"The model returned no text (finish_reason={choice.finish_reason}).{hint}")
        usage = completion.usage
        return ChatResponse(
            text=text, tool_calls=calls, model=completion.model or self._model, provider=self.provider,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            latency_ms=round(latency_ms, 1), stop_reason=choice.finish_reason,
            message=ChatMessage(role="assistant", content=text, tool_calls=calls,
                                provider_payload=_assistant_payload(message)),
        )


def to_openai_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Provider-neutral messages -> Chat Completions format (system message added by the caller)."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "tool":
            out.append({"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content})
        elif msg.role == "assistant":
            if msg.provider_payload is not None:
                out.append(msg.provider_payload)
            else:
                entry: dict[str, Any] = {"role": "assistant", "content": msg.content or None}
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                        for c in msg.tool_calls
                    ]
                out.append(entry)
        else:
            out.append({"role": "user", "content": msg.content})
    return out


def _assistant_payload(message: Any) -> dict[str, Any]:
    """The assistant message to send back: content, tool calls, and reasoning fields unmodified."""
    payload: dict[str, Any] = {"role": "assistant", "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in message.tool_calls
        ]
    extra = getattr(message, "model_extra", None) or {}
    for field in PASSTHROUGH_FIELDS:
        if extra.get(field) is not None:
            payload[field] = extra[field]
    return payload


def _parse_tool_call(call: Any) -> ToolCall:
    raw = call.function.arguments or "{}"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        return ToolCall(id=call.id, name=call.function.name, parse_error="arguments are not valid JSON")
    if not isinstance(args, dict):
        return ToolCall(id=call.id, name=call.function.name, parse_error="arguments must be a JSON object")
    return ToolCall(id=call.id, name=call.function.name, arguments=args)


def _mentions_tool_support(exc: openai.APIError) -> bool:
    status = getattr(exc, "status_code", None)
    text = str(getattr(exc, "message", "")).lower()
    return status in (400, 404) and "tool" in text and ("support" in text or "endpoint" in text)


def map_openai_error(exc: openai.APIError) -> LLMError:
    """Translate an SDK exception into our error types (no SDK message text, no secrets)."""
    if isinstance(exc, openai.APITimeoutError):  # subclass of APIConnectionError: check first
        return LLMTimeoutError("LLM request timed out. Try again or raise LLM_TIMEOUT_SECONDS.")
    if isinstance(exc, openai.APIConnectionError):
        return LLMConnectionError("Could not reach the LLM server. Check LLM_BASE_URL and that the server is running.")
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return LLMAuthError("The LLM server rejected the API key. Check OPENAI_API_KEY.")
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError("LLM rate limit reached. Wait a moment and try again.")
    if isinstance(exc, openai.APIStatusError) and exc.status_code == 402:
        # OpenRouter and some other gateways use 402 for an exhausted balance.
        return LLMBadRequestError("The LLM provider reports insufficient credits (HTTP 402). Add credits and retry.")
    if isinstance(exc, openai.NotFoundError):
        return LLMBadRequestError("The LLM server did not recognize the model. Check LLM_MODEL.")
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return LLMServerError(f"LLM server error (HTTP {exc.status_code}). Try again later.")
        return LLMBadRequestError(f"The LLM server rejected the request (HTTP {exc.status_code}).")
    return LLMError(f"LLM request failed ({type(exc).__name__}).")
