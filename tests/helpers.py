"""Small helpers shared by tests."""

from pathlib import Path

import pymupdf

from app.config import Settings

# Files in tests/fixtures/sample_project that ingestion should accept.
FIXTURE_FILES = {
    "README.md", "main.py", "auth.py", "database.py", "config.yaml", "data.json",
    "docs/troubleshooting.md", ".env.example",
}


def make_settings(root: Path, **overrides: object) -> Settings:
    """Settings for tests: ignore any real .env file in the working directory."""
    return Settings(_env_file=None, project_root=root, **overrides)  # type: ignore[call-arg]


def make_pdf(path: Path, pages: list[str]) -> None:
    """Write a small PDF with one line of text per page."""
    with pymupdf.open() as pdf:
        for text in pages:
            pdf.new_page().insert_text((72, 72), text)
        pdf.save(path)
