"""Build a project's vector index: Phase 1 pipeline -> embeddings -> Chroma.

Strategy for now: full rebuild. Each chunk also stores its file's
``file_modified_at`` and ``file_content_hash`` so incremental indexing
(re-embedding only changed files) can be added later.
"""

import hashlib
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings
from app.ingestion.pipeline import run_pipeline
from app.retrieval.embeddings import EmbeddingProvider, get_embedding_provider
from app.retrieval.vector_store import ChromaVectorStore, VectorStoreError, collection_name

logger = logging.getLogger(__name__)


class IndexExistsError(VectorStoreError):
    """The project is already indexed and no rebuild was requested."""


@dataclass
class IndexReport:
    """Summary of one indexing run."""

    project_name: str
    collection_name: str
    model_name: str
    files_indexed: int
    chunks_indexed: int
    files_skipped: int
    duration_seconds: float
    chunks_per_file_type: dict[str, int] = field(default_factory=dict)


def project_name_for(project_path: Path) -> str:
    """The project name used everywhere: the resolved directory name."""
    return project_path.resolve().name


def open_store(settings: Settings, project_path: Path, model_name: str | None) -> ChromaVectorStore:
    """The vector store for ``project_path`` (``model_name=None``: inspect only)."""
    name = collection_name(settings.chroma_collection_prefix, project_path)
    return ChromaVectorStore(settings.chroma_persist_dir, name, model_name)


def build_index(
    project_path: Path,
    rebuild: bool = True,
    settings: Settings | None = None,
    provider: EmbeddingProvider | None = None,
) -> IndexReport:
    """Index every chunk of ``project_path``.

    With ``rebuild=True`` the existing collection is deleted first. With
    ``rebuild=False`` an existing, non-empty index raises IndexExistsError
    (only full rebuilds are supported for now).
    """
    started = time.perf_counter()
    settings = (settings or get_settings()).model_copy(update={"project_root": project_path})
    provider = provider or get_embedding_provider(settings)
    store = open_store(settings, project_path, provider.name)

    if rebuild:
        store.delete_collection()
    elif store.exists() and store.count() > 0:
        raise IndexExistsError(
            f"Project '{project_name_for(project_path)}' is already indexed. "
            "Use --rebuild to replace the index."
        )

    result = run_pipeline(settings)
    file_info = {
        doc.id: {
            "file_modified_at": doc.modified_at,
            "file_content_hash": hashlib.sha256(doc.content.encode()).hexdigest(),
        }
        for doc in result.documents
    }

    batch_size = settings.embedding_batch_size
    for i in range(0, len(result.chunks), batch_size):
        batch = result.chunks[i:i + batch_size]
        embeddings = provider.embed_documents([c.content for c in batch])
        store.upsert_chunks(batch, embeddings, file_info)
        logger.info("Indexed %d/%d chunks", min(i + batch_size, len(result.chunks)), len(result.chunks))

    return IndexReport(
        project_name=result.project_name,
        collection_name=store.name,
        model_name=provider.name,
        files_indexed=len({c.file_path for c in result.chunks}),
        chunks_indexed=len(result.chunks),
        files_skipped=len(result.skipped),
        duration_seconds=round(time.perf_counter() - started, 2),
        chunks_per_file_type=dict(sorted(Counter(c.file_type for c in result.chunks).items())),
    )
