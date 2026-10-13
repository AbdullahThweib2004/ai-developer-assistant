"""Data models shared by the ingestion pipeline."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SkipReason(StrEnum):
    """Why a file was not ingested."""

    EXCLUDED_DIR = "excluded_dir"
    SENSITIVE = "sensitive"
    GITIGNORED = "gitignored"
    UNSUPPORTED_EXTENSION = "unsupported_extension"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    OUTSIDE_ROOT = "outside_root"
    UNREADABLE = "unreadable"
    PARSE_ERROR = "parse_error"


class SkippedFile(BaseModel):
    """A file (or directory) that was skipped, and why."""

    path: str
    reason: SkipReason
    detail: str = ""


class Document(BaseModel):
    """One parsed source file."""

    id: str
    file_path: str  # relative to the project root, POSIX style
    file_type: str  # extension without the dot, e.g. "py"
    project_name: str
    title: str | None = None
    content: str
    modified_at: datetime
    size_bytes: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    """A slice of a document's content, ready to be embedded later."""

    chunk_id: str
    document_id: str
    file_path: str
    file_type: str
    project_name: str
    chunk_index: int
    content: str
    start_char: int
    end_char: int
    metadata: dict[str, Any] = Field(default_factory=dict)
