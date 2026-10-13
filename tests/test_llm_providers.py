"""Tests for LLM providers, error mapping, retries and configuration (SDK clients are mocked)."""

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx2
import openai
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm import base
from app.llm.anthropic_provider import AnthropicProvider, map_anthropic_error
from app.llm.base import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConfigError,
    LLMConnectionError,
    LLMEmptyResponseError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMServerError,
    LLMTimeoutError,
)
from app.llm.factory import get_llm_provider
from app.llm.fake_provider import FakeLLMProvider
from app.llm.openai_compatible import OpenAICompatibleProvider, map_openai_error
from tests.helpers import make_settings

FAKE_KEY = "sk-test-DO-NOT-LEAK-1234567890"
REQUEST = httpx2.Request("POST", "https://api.example.com/v1/messages")


def status_error(sdk, cls_name: str, status: int):
    response = httpx2.Response(status, request=REQUEST)
    return getattr(sdk, cls_name)("boom " + FAKE_KEY, response=response, body=None)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base.time, "sleep", lambda _: None)


# ---- factory / configuration ------------------------------------------------


def test_factory_fake_needs_no_model(tmp_path: Path) -> None:
    assert isinstance(get_llm_provider(make_settings(tmp_path, llm_provider="fake")), FakeLLMProvider)


@pytest.mark.parametrize("provider", ["anthropic", "openai_compatible"])
def test_missing_model_is_a_clear_error(tmp_path: Path, provider: str) -> None:
    with pytest.raises(LLMConfigError, match="LLM_MODEL is not set"):
        get_llm_provider(make_settings(tmp_path, llm_provider=provider))


def test_missing_anthropic_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY is not set"):
        get_llm_provider(make_settings(tmp_path, llm_provider="anthropic", llm_model="m"))


def test_missing_openai_key_unless_local_base_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY is not set"):
        get_llm_provider(make_settings(tmp_path, llm_provider="openai_compatible", llm_model="m"))
    local = get_llm_provider(make_settings(
        tmp_path, llm_provider="openai_compatible", llm_model="llama3.2", llm_base_url="http://localhost:11434/v1"
    ))
    assert isinstance(local, OpenAICompatibleProvider)


def test_empty_env_values_count_as_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    monkeypatch.setenv("LLM_TEMPERATURE", "none")
    settings = make_settings(tmp_path)
    assert settings.llm_model is None and settings.anthropic_api_key is None
    assert settings.llm_temperature is None


def test_keys_never_appear_in_settings_repr_or_errors(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, llm_provider="anthropic", anthropic_api_key=FAKE_KEY)
    assert FAKE_KEY not in repr(settings) and FAKE_KEY not in str(settings.model_dump())
    with pytest.raises(LLMConfigError) as excinfo:
        get_llm_provider(settings)  # model missing
    assert FAKE_KEY not in str(excinfo.value)
    # SDK error text (which could echo request details) is never copied into our errors.
    for exc in (status_error(anthropic, "AuthenticationError", 401), status_error(anthropic, "BadRequestError", 400)):
        assert FAKE_KEY not in str(map_anthropic_error(exc))
    assert FAKE_KEY not in str(map_openai_error(status_error(openai, "AuthenticationError", 401)))


# ---- error mapping ------------------------------------------------------------


@pytest.mark.parametrize(
    ("make_exc", "expected"),
    [
        (lambda: anthropic.APITimeoutError(request=REQUEST), LLMTimeoutError),
        (lambda: anthropic.APIConnectionError(request=REQUEST), LLMConnectionError),
        (lambda: status_error(anthropic, "AuthenticationError", 401), LLMAuthError),
        (lambda: status_error(anthropic, "PermissionDeniedError", 403), LLMAuthError),
        (lambda: status_error(anthropic, "RateLimitError", 429), LLMRateLimitError),
        (lambda: status_error(anthropic, "NotFoundError", 404), LLMBadRequestError),
        (lambda: status_error(anthropic, "BadRequestError", 400), LLMBadRequestError),
        (lambda: status_error(anthropic, "InternalServerError", 500), LLMServerError),
        (lambda: status_error(anthropic, "OverloadedError", 529), LLMServerError),
    ],
)
def test_anthropic_error_mapping(make_exc, expected) -> None:
    assert isinstance(map_anthropic_error(make_exc()), expected)


@pytest.mark.parametrize(
    ("make_exc", "expected"),
    [
        (lambda: openai.APITimeoutError(request=REQUEST), LLMTimeoutError),
        (lambda: openai.APIConnectionError(request=REQUEST), LLMConnectionError),
        (lambda: status_error(openai, "AuthenticationError", 401), LLMAuthError),
        (lambda: status_error(openai, "RateLimitError", 429), LLMRateLimitError),
        (lambda: status_error(openai, "NotFoundError", 404), LLMBadRequestError),
        (lambda: status_error(openai, "InternalServerError", 503), LLMServerError),
    ],
)
def test_openai_error_mapping(make_exc, expected) -> None:
    assert isinstance(map_openai_error(make_exc()), expected)


# ---- Anthropic provider -------------------------------------------------------


def anthropic_message(text: str | None, stop_reason: str = "end_turn"):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        content=content, model="claude-test", stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=120, output_tokens=30),
    )


def anthropic_provider(create) -> tuple[AnthropicProvider, MagicMock]:
    client = MagicMock()
    client.messages.create.side_effect = create
    return AnthropicProvider("claude-test", SecretStr(FAKE_KEY), max_output_tokens=500, client=client), client


def test_anthropic_generate_reads_text_blocks_only() -> None:
    provider, client = anthropic_provider([anthropic_message("Answer [S1]")])
    response = provider.generate("system rules", "user question")
    assert response.text == "Answer [S1]"
    assert (response.input_tokens, response.output_tokens, response.provider) == (120, 30, "anthropic")
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"] == "system rules" and kwargs["max_tokens"] == 500
    assert kwargs["messages"] == [{"role": "user", "content": "user question"}]
    assert "temperature" not in kwargs  # current Claude models reject sampling params


def test_transient_error_is_retried_once() -> None:
    provider, client = anthropic_provider([status_error(anthropic, "RateLimitError", 429), anthropic_message("ok")])
    assert provider.generate("s", "u").text == "ok"
    assert client.messages.create.call_count == 2


def test_persistent_transient_error_gives_up_after_one_retry() -> None:
    provider, client = anthropic_provider([anthropic.APITimeoutError(request=REQUEST)] * 3)
    with pytest.raises(LLMTimeoutError):
        provider.generate("s", "u")
    assert client.messages.create.call_count == 2


def test_non_transient_error_is_not_retried() -> None:
    provider, client = anthropic_provider([status_error(anthropic, "AuthenticationError", 401)])
    with pytest.raises(LLMAuthError):
        provider.generate("s", "u")
    assert client.messages.create.call_count == 1


def test_empty_and_refused_responses() -> None:
    provider, _ = anthropic_provider([anthropic_message(None, stop_reason="max_tokens")])
    with pytest.raises(LLMEmptyResponseError, match="LLM_MAX_OUTPUT_TOKENS"):
        provider.generate("s", "u")
    provider, _ = anthropic_provider([anthropic_message(None, stop_reason="refusal")])
    with pytest.raises(LLMRefusalError):
        provider.generate("s", "u")


def test_logs_contain_metadata_but_no_content(caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = anthropic_provider([anthropic_message("SECRET ANSWER TEXT")])
    with caplog.at_level(logging.DEBUG):
        provider.generate("SYSTEM PROMPT TEXT", "RETRIEVED CODE TEXT")
    logged = caplog.text
    assert "claude-test" in logged and "input_tokens=120" in logged
    for content in ("SYSTEM PROMPT TEXT", "RETRIEVED CODE TEXT", "SECRET ANSWER TEXT", FAKE_KEY):
        assert content not in logged


# ---- OpenAI-compatible provider ----------------------------------------------


def openai_completion(text: str | None, finish_reason: str = "stop", refusal: str | None = None):
    message = SimpleNamespace(content=text, refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        model="gpt-test", usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10),
    )


def openai_provider(create, **kwargs) -> tuple[OpenAICompatibleProvider, MagicMock]:
    client = MagicMock()
    client.chat.completions.create.side_effect = create
    return OpenAICompatibleProvider("gpt-test", SecretStr(FAKE_KEY), client=client, **kwargs), client


def test_openai_generate_and_token_parameter_choice() -> None:
    provider, client = openai_provider([openai_completion("hi")], max_output_tokens=300, temperature=0.1)
    response = provider.generate("s", "u")
    assert (response.text, response.input_tokens, response.output_tokens) == ("hi", 50, 10)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["max_completion_tokens"] == 300 and "max_tokens" not in kwargs
    assert kwargs["temperature"] == 0.1
    assert kwargs["messages"][0] == {"role": "system", "content": "s"}

    local, local_client = openai_provider([openai_completion("hi")], base_url="http://localhost:11434/v1", temperature=None)
    local.generate("s", "u")
    local_kwargs = local_client.chat.completions.create.call_args.kwargs
    assert local_kwargs["max_tokens"] == 1024 and "max_completion_tokens" not in local_kwargs
    assert "temperature" not in local_kwargs


def test_openai_empty_and_refusal() -> None:
    provider, _ = openai_provider([openai_completion("", finish_reason="length")])
    with pytest.raises(LLMEmptyResponseError, match="LLM_MAX_OUTPUT_TOKENS"):
        provider.generate("s", "u")
    provider, _ = openai_provider([openai_completion(None, refusal="I can't help with that")])
    with pytest.raises(LLMRefusalError):
        provider.generate("s", "u")


# ---- fake provider ------------------------------------------------------------


def test_fake_provider_records_calls_and_cycles_responses() -> None:
    fake = FakeLLMProvider(["one", "two"])
    assert [fake.generate("s", "u").text for _ in range(3)] == ["one", "two", "two"]
    assert len(fake.calls) == 3
    assert FakeLLMProvider(lambda s, u: u.upper()).generate("s", "abc").text == "ABC"
    with pytest.raises(LLMTimeoutError):
        FakeLLMProvider(error=LLMTimeoutError("t")).generate("s", "u")


# ---- optional real provider --------------------------------------------------


@pytest.mark.slow
def test_real_provider_round_trip() -> None:
    """Calls the configured provider for real (reads .env). Skipped when none is configured."""
    settings = Settings()
    if settings.llm_provider == "fake":
        pytest.skip("LLM_PROVIDER is fake")
    try:
        provider = get_llm_provider(settings)
    except LLMConfigError as exc:
        pytest.skip(f"No real LLM configured: {exc}")
    response = provider.generate("Reply with exactly one word: pong", "ping")
    assert "pong" in response.text.lower()
