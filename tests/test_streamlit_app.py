"""Smoke test for the Streamlit UI using Streamlit's AppTest (fake providers, no network)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.config import Settings

APP = str(Path(__file__).parents[1] / "app" / "ui" / "streamlit_app.py")


@pytest.fixture
def ui_env(index_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(index_settings.chroma_persist_dir))
    monkeypatch.setenv("PROJECT_ROOT", str(index_settings.project_root))
    monkeypatch.chdir(index_settings.project_root.parent)
    return index_settings


def ask_in_ui(question: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60).run()
    if at.sidebar.button[0].label == "Index project":
        at.sidebar.button[0].click().run()
    at.text_area[0].input(question)
    at.main.button[0].click().run()
    return at


def test_index_and_ask_flow(ui_env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    at = ask_in_ui("How does verify_user_credentials check a password?")
    assert not at.exception
    assert [m.label for m in at.sidebar.metric] == ["Chunks", "Files"]
    assert any("[S1]" in m.value for m in at.main.markdown)
    assert at.main.expander  # retrieved chunk panels
    assert any("fake-llm" in c.value for c in at.main.caption)


def test_missing_llm_model_is_a_friendly_error(ui_env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-never-shown")
    at = ask_in_ui("How does verify_user_credentials check a password?")
    assert not at.exception
    assert [e.value for e in at.error] == ["LLM_MODEL is not set. Set it in .env to the model name for LLM_PROVIDER=anthropic."]
    elements = [*at.main.markdown, *at.sidebar.caption, *at.error]
    rendered = " ".join(str(el.value) for el in elements)
    assert "sk-never-shown" not in rendered


def test_unindexed_project_shows_warning(ui_env: Settings) -> None:
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert "not indexed" in at.sidebar.warning[0].value
