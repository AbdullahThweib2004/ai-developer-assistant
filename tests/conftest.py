"""Shared test fixtures."""

import shutil
from pathlib import Path

import pytest

from app.config import Settings
from tests.helpers import make_settings

FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A fresh, writable copy of the sample project for each test.

    The fake ``.env`` is created here rather than committed, so the repository
    never contains a .env file, not even a dummy one.
    """
    dest = tmp_path / "sample_project"
    shutil.copytree(FIXTURE_PROJECT, dest, symlinks=True)
    (dest / ".env").write_text("DUMMY_SECRET=not-a-real-secret-for-tests\n")
    return dest



@pytest.fixture
def index_settings(project: Path, tmp_path: Path) -> Settings:
    """Settings for retrieval tests: fake embeddings and a temp Chroma directory."""
    return make_settings(
        project,
        embedding_provider="fake",
        chroma_persist_dir=tmp_path / "chroma",
        chunk_size=300,
        chunk_overlap=50,
    )


# Settings read from the environment that would change test behavior.
_ISOLATED_ENV = (
    "PROJECT_ROOT", "CHUNK_SIZE", "CHUNK_OVERLAP", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL",
    "CHROMA_PERSIST_DIR", "CHROMA_COLLECTION_PREFIX", "RETRIEVAL_TOP_K", "RETRIEVAL_MIN_SCORE",
    "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_TEMPERATURE", "LLM_MAX_OUTPUT_TOKENS",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "RAG_MAX_CONTEXT_CHARS",
    "MCP_ALLOWED_PROJECT_ROOTS", "MCP_MAX_FILE_CHARS", "MCP_MAX_TOP_K",
    "GITHUB_TOKEN", "GITHUB_API_URL", "GITHUB_DEFAULT_REPO", "GITHUB_ALLOWED_REPOS",
    "GITHUB_MAX_RESULTS", "GITHUB_MAX_PAGES", "GITHUB_MAX_BODY_CHARS", "GITHUB_MAX_COMMENTS",
)


@pytest.fixture(autouse=True)
def isolated_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's shell variables out of tests (slow tests use the real config)."""
    if request.node.get_closest_marker("slow"):
        return
    for name in _ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)
