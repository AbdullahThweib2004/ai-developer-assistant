"""Tests for the Chroma wrapper."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ingestion.models import Chunk
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.vector_store import (
    ChromaVectorStore,
    EmbeddingModelMismatchError,
    ProjectNotIndexedError,
    VectorStoreError,
    collection_name,
    to_chroma_metadata,
)

PROVIDER = FakeEmbeddingProvider()


def make_chunk(i: int, content: str, file_path: str = "a.py", **metadata: object) -> Chunk:
    return Chunk(
        chunk_id=f"id-{file_path}-{i}",
        document_id=f"doc-{file_path}",
        file_path=file_path,
        file_type=file_path.rsplit(".", 1)[-1],
        project_name="proj",
        chunk_index=i,
        content=content,
        start_char=i * 10,
        end_char=i * 10 + len(content),
        metadata={"title": file_path, **metadata},
    )


@pytest.fixture
def store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(tmp_path / "chroma", "project_test", PROVIDER.name)


def add(store: ChromaVectorStore, chunks: list[Chunk], extra: dict | None = None) -> None:
    store.upsert_chunks(chunks, PROVIDER.embed_documents([c.content for c in chunks]), extra)


@pytest.mark.parametrize(
    ("folder", "expected_middle"),
    [("sample_project", "sample_project"), ("My Repo!.v2", "my_repo_v2"), ("...", "default")],
)
def test_collection_name_is_sanitized(tmp_path: Path, folder: str, expected_middle: str) -> None:
    name = collection_name("project", tmp_path / folder)
    assert re.fullmatch(rf"project_{re.escape(expected_middle)}_[0-9a-f]{{8}}", name)


def test_collection_name_respects_chroma_limits(tmp_path: Path) -> None:
    name = collection_name("project", tmp_path / ("x" * 300))
    assert 3 <= len(name) <= 63
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*[a-z0-9]", name)


def test_same_folder_name_in_different_places_gets_different_collections(tmp_path: Path) -> None:
    a = collection_name("project", tmp_path / "team_a" / "api")
    b = collection_name("project", tmp_path / "team_b" / "api")
    assert a != b
    assert a.startswith("project_api_") and b.startswith("project_api_")
    assert collection_name("project", tmp_path / "team_a" / "x" / ".." / "api") == a  # resolved


def test_to_chroma_metadata_flattens_unsupported_values() -> None:
    when = datetime(2026, 1, 2, tzinfo=UTC)
    flat = to_chroma_metadata(
        {"s": "x", "i": 1, "f": 1.5, "b": True, "none": None, "when": when,
         "pages": [1, 2], "empty": [], "nested": {"a": 1}}
    )
    assert flat == {"s": "x", "i": 1, "f": 1.5, "b": True, "when": when.isoformat(),
                    "pages": "1,2", "nested": '{"a": 1}'}


def test_upsert_count_and_metadata_round_trip(store: ChromaVectorStore) -> None:
    pdf_chunk = make_chunk(0, "page text", "doc.pdf", pages=[2, 3])
    py_chunk = make_chunk(1, "def login(): pass", "auth.py")
    add(store, [pdf_chunk, py_chunk], {"doc-auth.py": {"file_content_hash": "abc", "missing": None}})

    assert store.count() == 2
    assert store.list_indexed_files() == ["auth.py", "doc.pdf"]

    hit = store.query(PROVIDER.embed_query("def login(): pass"), top_k=1)[0]
    assert hit.chunk_id == py_chunk.chunk_id
    assert hit.content == py_chunk.content
    assert hit.metadata == {
        "document_id": "doc-auth.py", "file_path": "auth.py", "file_type": "py",
        "project_name": "proj", "chunk_index": 1, "start_char": 10, "end_char": 27,
        "title": "auth.py", "file_content_hash": "abc",
    }
    assert hit.similarity == pytest.approx(1.0, abs=1e-5)

    pdf_hit = store.query(PROVIDER.embed_query("page text"), top_k=1)[0]
    assert pdf_hit.metadata["pages"] == "2,3"
    assert (pdf_hit.metadata["page_start"], pdf_hit.metadata["page_end"]) == (2, 3)


def test_query_results_are_ordered_and_filterable(store: ChromaVectorStore) -> None:
    add(store, [
        make_chunk(0, "database connection retry backoff", "db.py"),
        make_chunk(1, "database connection settings", "config.yaml"),
        make_chunk(2, "banana bread recipe", "notes.md"),
    ])
    hits = store.query(PROVIDER.embed_query("database connection retry"), top_k=3)
    assert [h.metadata["file_path"] for h in hits] == ["db.py", "config.yaml", "notes.md"]
    assert [h.similarity for h in hits] == sorted((h.similarity for h in hits), reverse=True)

    only_yaml = store.query(PROVIDER.embed_query("database"), top_k=3, where={"file_type": "yaml"})
    assert [h.metadata["file_path"] for h in only_yaml] == ["config.yaml"]


def test_upsert_same_ids_does_not_duplicate(store: ChromaVectorStore) -> None:
    chunks = [make_chunk(i, f"chunk {i}") for i in range(3)]
    add(store, chunks)
    add(store, chunks)
    assert store.count() == 3


def test_model_mismatch_raises(tmp_path: Path) -> None:
    store = ChromaVectorStore(tmp_path / "chroma", "project_test", "model-a")
    store.upsert_chunks([make_chunk(0, "x")], [[1.0, 0.0]])
    other = ChromaVectorStore(tmp_path / "chroma", "project_test", "model-b")
    with pytest.raises(EmbeddingModelMismatchError, match="--rebuild"):
        other.query([1.0, 0.0], top_k=1)
    with pytest.raises(EmbeddingModelMismatchError):
        other.upsert_chunks([make_chunk(1, "y")], [[0.0, 1.0]])


def test_missing_collection_raises_not_indexed(store: ChromaVectorStore) -> None:
    assert not store.exists()
    with pytest.raises(ProjectNotIndexedError, match="not indexed"):
        store.count()
    with pytest.raises(ProjectNotIndexedError):
        store.query([0.0] * PROVIDER.dimension, top_k=3)


def test_delete_collection(store: ChromaVectorStore) -> None:
    add(store, [make_chunk(0, "x")])
    store.delete_collection()
    assert not store.exists()
    store.delete_collection()  # deleting twice is fine


def test_inspect_only_store_reads_but_never_writes(store: ChromaVectorStore, tmp_path: Path) -> None:
    add(store, [make_chunk(0, "x")])
    inspector = ChromaVectorStore(tmp_path / "chroma", "project_test", None)
    assert inspector.stored_model() == PROVIDER.name
    assert inspector.count() == 1
    with pytest.raises(VectorStoreError):
        inspector.query([0.0] * PROVIDER.dimension, top_k=1)
    with pytest.raises(ProjectNotIndexedError):
        ChromaVectorStore(tmp_path / "chroma", "project_other", None).count()
