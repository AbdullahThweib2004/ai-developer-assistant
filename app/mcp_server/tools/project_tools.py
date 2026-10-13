"""Read-only project tools: thin wrappers over existing ingestion/retrieval code.

These functions contain no MCP specifics; ``app.mcp_server.server`` registers
them as tools. Every project path goes through ``resolve_project`` and every
file through ``check_project_file`` (the ingestion rules).
"""

from collections import Counter
from pathlib import Path

from app.config import Settings
from app.ingestion.document_parser import ParseError, file_type_for, parse_document
from app.ingestion.file_loader import FileLoader
from app.mcp_server.schemas import (
    FileReadResult,
    IndexStatsResult,
    ProjectFile,
    ProjectFilesResult,
    SearchHit,
    SearchResult,
)
from app.mcp_server.security import (
    UNTRUSTED_NOTE,
    SecurityError,
    allowed_roots,
    check_project_file,
    resolve_project,
    wrap_untrusted,
)
from app.retrieval.embeddings import EmbeddingProvider, get_embedding_provider
from app.retrieval.indexer import index_stats
from app.retrieval.retriever import InvalidQueryError, Retriever

MAX_LISTED_FILES = 1000
LINE_PREFIX_WIDTH = 6  # "  123 | "


class ProjectTools:
    """Holds settings, allowed roots and a lazily created embedding provider."""

    def __init__(self, settings: Settings, embedding_provider: EmbeddingProvider | None = None) -> None:
        self.settings = settings
        self.roots = allowed_roots(settings)  # raises ServerConfigError if empty
        self._embedding_provider = embedding_provider

    @property
    def embedding_provider(self) -> EmbeddingProvider:
        """Created on first search; the model itself loads on first embed."""
        if self._embedding_provider is None:
            self._embedding_provider = get_embedding_provider(self.settings)
        return self._embedding_provider

    def _project(self, project_path: str) -> Path:
        return resolve_project(project_path, self.roots)

    def _loader(self, project: Path) -> FileLoader:
        return FileLoader(self.settings.model_copy(update={"project_root": project}), root=project)

    # ---- tools ------------------------------------------------------------

    def get_index_stats(self, project_path: str) -> IndexStatsResult:
        project = self._project(project_path)
        stats = index_stats(self.settings, project)
        return IndexStatsResult(
            project_name=project.name,
            collection_name=stats.collection_name,
            chunk_count=stats.chunk_count,
            embedding_model=stats.model_name,
            files_indexed=len(stats.files),
            files=stats.files,
        )

    def search_project(
        self, project_path: str, query: str, top_k: int = 5, file_types: list[str] | None = None
    ) -> SearchResult:
        project = self._project(project_path)
        if not 1 <= top_k <= self.settings.mcp_max_top_k:
            raise InvalidQueryError(f"top_k must be between 1 and {self.settings.mcp_max_top_k}")
        retriever = Retriever.for_project(project, self.settings, self.embedding_provider)
        chunks = retriever.search(query, top_k=top_k, min_score=self.settings.retrieval_min_score,
                                  file_types=file_types)
        hits = []
        for rank, chunk in enumerate(chunks, start=1):
            location = f"chars {chunk.start_char}-{chunk.end_char}"
            if chunk.metadata.get("pages"):
                location += f", pages {chunk.metadata['pages']}"
            hits.append(SearchHit(
                rank=rank, file_path=chunk.file_path, file_type=chunk.file_type,
                chunk_index=chunk.chunk_index, score=chunk.score, location=location,
                content=wrap_untrusted(chunk.content, chunk.file_path),
            ))
        return SearchResult(project_name=project.name, query=query.strip(), hits=hits, note=UNTRUSTED_NOTE)

    def read_project_file(
        self, project_path: str, file_path: str, start_line: int | None = None, end_line: int | None = None
    ) -> FileReadResult:
        project = self._project(project_path)
        if start_line is not None and end_line is not None and end_line < start_line:
            raise InvalidQueryError("end_line must be greater than or equal to start_line")
        loader = self._loader(project)
        resolved = check_project_file(loader, file_path)
        rel_path = loader.relative_path(resolved)
        try:
            # Same decoding/extraction as ingestion (UTF-8 with fallback, PDF text).
            text = parse_document(resolved, project, project.name).content
        except ParseError:
            raise SecurityError("This file could not be read (parse_error).") from None

        lines = text.splitlines()
        total = len(lines)
        first = start_line or 1
        last = min(end_line or total, total)
        if total and first > total:
            raise InvalidQueryError(f"start_line is beyond the end of the file ({total} lines)")

        out: list[str] = []
        used = 0
        truncated = False
        shown_last = first - 1
        for number in range(first, last + 1):
            line = f"{number:>{LINE_PREFIX_WIDTH}} | {lines[number - 1]}"
            if used + len(line) + 1 > self.settings.mcp_max_file_chars:
                truncated = True
                break
            out.append(line)
            used += len(line) + 1
            shown_last = number
        if not out and truncated:  # a single line longer than the limit: cut that line
            line = f"{first:>{LINE_PREFIX_WIDTH}} | {lines[first - 1]}"
            out.append(line[: self.settings.mcp_max_file_chars])
            shown_last = first

        return FileReadResult(
            file_path=rel_path,
            start_line=first,
            end_line=shown_last,
            total_lines=total,
            truncated=truncated,
            content=wrap_untrusted("\n".join(out), rel_path),
            note=UNTRUSTED_NOTE,
        )

    def list_project_files(self, project_path: str, file_types: list[str] | None = None) -> ProjectFilesResult:
        project = self._project(project_path)
        loader = self._loader(project)
        result = loader.discover()
        wanted = {t.lower().lstrip(".") for t in file_types} if file_types else None
        files = []
        for path in sorted(result.files):
            file_type = file_type_for(path)
            if wanted is None or file_type in wanted:
                files.append(ProjectFile(path=loader.relative_path(path), file_type=file_type,
                                         size_bytes=path.stat().st_size))
        return ProjectFilesResult(
            project_name=project.name,
            total_files=len(files),
            files=files[:MAX_LISTED_FILES],
            truncated=len(files) > MAX_LISTED_FILES,
            # Counts only: listing skipped names could reveal which secret files exist.
            skipped_by_reason=dict(sorted(Counter(s.reason.value for s in result.skipped).items())),
        )
