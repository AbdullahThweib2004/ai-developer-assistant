"""Build the configured LLM provider, with clear errors for missing settings."""

from app.config import Settings
from app.llm.base import LLMConfigError, LLMProvider


def get_llm_provider(settings: Settings) -> LLMProvider:
    """Return the provider selected by ``LLM_PROVIDER``.

    Error messages name the variable to set, never its value.
    """
    if settings.llm_provider == "fake":
        from app.llm.fake_provider import FakeLLMProvider

        return FakeLLMProvider(model=settings.llm_model or "fake-llm")

    if not settings.llm_model:
        raise LLMConfigError(
            f"LLM_MODEL is not set. Set it in .env to the model name for LLM_PROVIDER={settings.llm_provider}."
        )

    if settings.llm_provider == "anthropic":
        if settings.anthropic_api_key is None:
            raise LLMConfigError("ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example).")
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model=settings.llm_model,
            api_key=settings.anthropic_api_key,
            max_output_tokens=settings.llm_max_output_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    if settings.llm_provider == "openai_compatible":
        # OpenAI itself needs a key; local servers (e.g. Ollama via LLM_BASE_URL) usually don't.
        if settings.openai_api_key is None and not settings.llm_base_url:
            raise LLMConfigError(
                "OPENAI_API_KEY is not set. Add it to .env, or set LLM_BASE_URL for a local server such as Ollama."
            )
        from app.llm.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.llm_base_url,
            max_output_tokens=settings.llm_max_output_tokens,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    # Extension point: add new providers here.
    raise LLMConfigError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
