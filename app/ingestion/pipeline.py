"""The Phase 1 pipeline: discover -> parse -> chunk."""

from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.ingestion.chunker import CharacterChunker, Chunker
from app.ingestion.document_parser import parse_documents
from app.ingestion.file_loader import FileLoader
from app.ingestion.models import Chunk, Document, SkippedFile


@dataclass
class PipelineResult:
    """Everything produced by one load -> parse -> chunk run."""

    root: Path
    project_name: str
    files_found: int
    skipped: list[SkippedFile] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)


def run_pipeline(settings: Settings, chunker: Chunker | None = None) -> PipelineResult:
    """Discover, parse and chunk every eligible file under ``settings.project_root``."""
    loader = FileLoader(settings)
    loaded = loader.discover()
    project_name = loader.root.name

    documents, parse_skips = parse_documents(loaded.files, loader.root, project_name)

    chunker = chunker or CharacterChunker(settings.chunk_size, settings.chunk_overlap)
    chunks = [chunk for doc in documents for chunk in chunker.chunk(doc)]

    return PipelineResult(
        root=loader.root,
        project_name=project_name,
        files_found=len(loaded.files),
        skipped=loaded.skipped + parse_skips,
        documents=documents,
        chunks=chunks,
    )
