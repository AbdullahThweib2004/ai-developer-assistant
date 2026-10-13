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
