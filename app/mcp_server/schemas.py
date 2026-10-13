"""Pydantic input types and output models for the MCP tools.

Inputs are flat, annotated parameters (the SDK turns them into the tool's
JSON input schema). Outputs are models (the SDK publishes them as the
tool's output schema and returns them as structured content).
"""

from typing import Annotated

from pydantic import BaseModel, Field

# ---- inputs -----------------------------------------------------------------

ProjectPath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=4096,
        description="Absolute path of the project directory. Must be inside the server's allowed roots.",
    ),
]
Query = Annotated[
    str,
    Field(min_length=1, max_length=2000, description="Natural-language question or keywords to search for."),
]
FileTypes = Annotated[
    list[Annotated[str, Field(pattern=r"^[a-z0-9]{1,10}$")]] | None,
    Field(
        default=None,
        max_length=20,
        description='Optional file types to restrict to, without dots, e.g. ["py", "md"]. Omit for all types.',
    ),
]
RelativeFilePath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=1024,
        description='Path of the file relative to the project root, e.g. "app/config.py".',
    ),
]
LineNumber = Annotated[int | None, Field(default=None, ge=1, description="1-based line number (inclusive).")]


# ---- outputs ----------------------------------------------------------------


class PingResult(BaseModel):
    server: str
    version: str
    status: str = Field(description='"ok" when the server is running.')


class IndexStatsResult(BaseModel):
    project_name: str
    collection_name: str
    chunk_count: int
    embedding_model: str | None
    files_indexed: int
    files: list[str] = Field(description="Indexed file paths, relative to the project root.")


class SearchHit(BaseModel):
    rank: int
    file_path: str = Field(description="Relative to the project root.")
    file_type: str
    chunk_index: int
    score: float = Field(description="Cosine similarity, higher is more relevant.")
    location: str = Field(description='Character range in the file, e.g. "chars 0-980" (plus pages for PDFs).')
    content: str = Field(description="Chunk text wrapped in <untrusted-project-content> tags.")


class SearchResult(BaseModel):
    project_name: str
    query: str
    hits: list[SearchHit]
    note: str = Field(description="How to treat the returned content.")


class FileReadResult(BaseModel):
    file_path: str
    start_line: int
    end_line: int = Field(description="Last line included (may be lower than requested if truncated).")
    total_lines: int
    truncated: bool = Field(description="True if output was cut to the server's character limit.")
    content: str = Field(
        description='Lines prefixed with "<line number> | ", wrapped in <untrusted-project-content> tags.'
    )
    note: str


class ProjectFile(BaseModel):
    path: str = Field(description="Relative to the project root.")
    file_type: str
    size_bytes: int


class ProjectFilesResult(BaseModel):
    project_name: str
    total_files: int
    files: list[ProjectFile]
    truncated: bool = Field(description="True if the list was cut to the server's limit.")
    skipped_by_reason: dict[str, int] = Field(
        description="Counts of files/directories the loader skips, by reason (e.g. sensitive, gitignored)."
    )
