"""Tests for indexing and retrieval on the sample project (fake embeddings, no network)."""


import pytest

from app.cli import main
from app.config import Settings
from app.ingestion.pipeline import run_pipeline
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.indexer import IndexExistsError, build_index, open_store
from app.retrieval.retriever import InvalidQueryError, Retriever
from app.retrieval.vector_store import ProjectNotIndexedError


@pytest.fixture
def indexed(index_settings: Settings) -> Retriever:
    build_index(index_settings.project_root, settings=index_settings)
    return Retriever.for_project(index_settings.project_root, index_settings)


def test_index_report_matches_pipeline(index_settings: Settings) -> None:
    expected = run_pipeline(index_settings)
    report = build_index(index_settings.project_root, settings=index_settings)

    assert report.chunks_indexed == len(expected.chunks)
    assert report.files_indexed == len(expected.documents)
    assert report.model_name == FakeEmbeddingProvider().name
    assert report.collection_name.startswith("project_sample_project_")
    assert sum(report.chunks_per_file_type.values()) == report.chunks_indexed
    assert report.duration_seconds >= 0

    store = open_store(index_settings, index_settings.project_root, report.model_name)
    assert store.count() == len(expected.chunks)
    assert store.list_indexed_files() == sorted(d.file_path for d in expected.documents)


def test_indexed_chunks_store_file_info_for_incremental_indexing(indexed: Retriever) -> None:
    result = indexed.search("verify user credentials", top_k=1)[0]
    assert len(result.metadata["file_content_hash"]) == 64
    assert "T" in result.metadata["file_modified_at"]  # ISO timestamp


def test_rebuild_does_not_duplicate(index_settings: Settings) -> None:
    first = build_index(index_settings.project_root, settings=index_settings)
    build_index(index_settings.project_root, rebuild=True, settings=index_settings)
    store = open_store(index_settings, index_settings.project_root, first.model_name)
    assert store.count() == first.chunks_indexed


def test_index_without_rebuild_refuses_existing_index(index_settings: Settings) -> None:
    build_index(index_settings.project_root, rebuild=False, settings=index_settings)  # fresh: ok
    with pytest.raises(IndexExistsError, match="--rebuild"):
        build_index(index_settings.project_root, rebuild=False, settings=index_settings)


def test_search_results_are_ranked(indexed: Retriever) -> None:
    results = indexed.search("verify user credentials password hash", top_k=5)
    assert 0 < len(results) <= 5
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
    assert results[0].file_path == "auth.py"
    top = results[0]
    assert top.content and top.end_char > top.start_char
    assert top.metadata["project_name"] == "sample_project"


def test_top_k_and_min_score(indexed: Retriever) -> None:
    assert len(indexed.search("database", top_k=2)) == 2
    all_results = indexed.search("database connection", top_k=50)
    threshold = all_results[2].score
    filtered = indexed.search("database connection", top_k=50, min_score=threshold)
    assert filtered and all(r.score >= threshold for r in filtered)
    assert len(filtered) < len(all_results)
    assert indexed.search("database connection", min_score=1.0) == []


def test_file_type_filter(indexed: Retriever) -> None:
    results = indexed.search("database host and port", top_k=10, file_types=["yaml"])
    assert results and {r.file_type for r in results} == {"yaml"}
    mixed = indexed.search("database", top_k=20, file_types=["md", ".py"])
    assert {r.file_type for r in mixed} <= {"md", "py"}


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_empty_query_is_rejected(indexed: Retriever, query: str) -> None:
    with pytest.raises(InvalidQueryError):
        indexed.search(query)


def test_invalid_top_k_is_rejected(indexed: Retriever) -> None:
    with pytest.raises(InvalidQueryError):
        indexed.search("database", top_k=0)


def test_search_unindexed_project_raises(index_settings: Settings) -> None:
    retriever = Retriever.for_project(index_settings.project_root, index_settings)
    with pytest.raises(ProjectNotIndexedError, match="not indexed"):
        retriever.search("anything")


def test_cli_index_search_stats(index_settings: Settings, monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(index_settings.chroma_persist_dir))
    monkeypatch.chdir(index_settings.project_root.parent)  # no stray .env picked up
    path = str(index_settings.project_root)

    assert main(["search", "--path", path, "anything"]) == 1
    assert "not indexed" in capsys.readouterr().err

    assert main(["index", "--path", path]) == 0
    assert "project_sample_project_" in capsys.readouterr().out
    assert main(["index", "--path", path]) == 1  # exists, no --rebuild
    assert main(["index", "--path", path, "--rebuild"]) == 0

    assert main(["search", "--path", path, "verify user credentials", "--top-k", "2", "--type", "py"]) == 0
    out = capsys.readouterr().out
    assert "1. [" in out and "auth.py" in out

    assert main(["stats", "--path", path]) == 0
    out = capsys.readouterr().out
    assert "fake-hashed-bow-256" in out and "auth.py" in out
