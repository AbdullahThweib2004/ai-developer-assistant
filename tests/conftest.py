"""Shared test fixtures."""

import shutil
from pathlib import Path

import pytest

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

