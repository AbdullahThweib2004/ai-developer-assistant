"""Turn files into :class:`Document` objects.

Parsing is read-only: JSON/YAML are validated with safe parsers, nothing is
evaluated or imported. A file that cannot be parsed raises :class:`ParseError`;
:func:`parse_documents` catches it so one bad file never stops a run.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf
import yaml

from app.ingestion.models import Document, SkippedFile, SkipReason

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when a file cannot be turned into a Document."""


def parse_document(path: Path, root: Path, project_name: str) -> Document:
    """Parse a single file located inside ``root``."""
    rel_path = path.relative_to(root).as_posix()
    suffix = path.suffix.lower()
    file_type = file_type_for(path)
    metadata: dict[str, Any] = {}

    try:
        if suffix == ".pdf":
            content = _read_pdf(path, metadata)
        else:
            content = _read_text(path, metadata)
    except ParseError:
        raise
    except Exception as exc:  # any library/OS failure becomes a ParseError
        raise ParseError(f"{type(exc).__name__}: {exc}") from exc

    if suffix == ".json":
        _validate_syntax(content, metadata, lambda text: json.loads(text))
    elif suffix in (".yaml", ".yml"):
        _validate_syntax(content, metadata, lambda text: list(yaml.safe_load_all(text)))

    title = _markdown_title(content) if suffix == ".md" else None
    stat = path.stat()
    return Document(
        id=_document_id(project_name, rel_path),
        file_path=rel_path,
        file_type=file_type,
        project_name=project_name,
        title=title or path.name,
        content=content,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        size_bytes=stat.st_size,
        metadata=metadata,
    )


def parse_documents(
    paths: list[Path], root: Path, project_name: str
) -> tuple[list[Document], list[SkippedFile]]:
    """Parse many files; failures are collected as ``parse_error`` skips."""
    documents: list[Document] = []
    skipped: list[SkippedFile] = []
    for path in paths:
        try:
            documents.append(parse_document(path, root, project_name))
        except ParseError as exc:
            rel = path.relative_to(root).as_posix()
            logger.warning("Failed to parse %s: %s", rel, exc)
            skipped.append(SkippedFile(path=rel, reason=SkipReason.PARSE_ERROR, detail=str(exc)))
    return documents, skipped


# ---- format-specific readers ---------------------------------------------


def _read_text(path: Path, metadata: dict[str, Any]) -> str:
    """Decode as UTF-8 (BOM tolerated); fall back to replacing bad bytes."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
        metadata["decoding_replaced_chars"] = False
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        metadata["decoding_replaced_chars"] = True
    return text


def _read_pdf(path: Path, metadata: dict[str, Any]) -> str:
    """Extract text page by page, recording where each page starts and ends."""
    pages: list[dict[str, int]] = []
    parts: list[str] = []
    offset = 0
    separator = "\n\n"
    with pymupdf.open(path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text()
            if parts:
                offset += len(separator)
            pages.append({"page": page_number, "start_char": offset, "end_char": offset + len(text)})
            parts.append(text)
            offset += len(text)
        metadata["page_count"] = pdf.page_count
    metadata["pages"] = pages
    return separator.join(parts)


def _validate_syntax(content: str, metadata: dict[str, Any], loader: Any) -> None:
    """Check that structured text parses; record the result instead of raising."""
    try:
        loader(content)
        metadata["valid_syntax"] = True
    except Exception as exc:  # json.JSONDecodeError, yaml.YAMLError, ...
        metadata["valid_syntax"] = False
        metadata["syntax_error"] = str(exc).splitlines()[0][:200]


def file_type_for(path: Path) -> str:
    """Extension without the dot; env templates like ``.env.example`` are "env"."""
    if path.name.lower().startswith(".env"):
        return "env"
    return path.suffix.lower().lstrip(".")


def _markdown_title(content: str) -> str | None:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None


def _document_id(project_name: str, rel_path: str) -> str:
    return hashlib.sha256(f"{project_name}:{rel_path}".encode()).hexdigest()[:32]
