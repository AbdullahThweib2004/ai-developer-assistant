"""Tests for parsing each supported file type."""

from pathlib import Path

import pytest

from app.ingestion.document_parser import ParseError, parse_document, parse_documents
from app.ingestion.models import SkipReason
from tests.helpers import make_pdf


def parse(project: Path, name: str):
    return parse_document(project / name, project, "sample_project")


def test_markdown_title_and_metadata(project: Path) -> None:
    doc = parse(project, "README.md")
    assert doc.title == "Sample Project"
    assert doc.file_path == "README.md"
    assert doc.file_type == "md"
    assert doc.project_name == "sample_project"
    assert doc.size_bytes == (project / "README.md").stat().st_size
    assert "tiny fake repository" in doc.content


def test_python_file_uses_filename_as_title(project: Path) -> None:
    doc = parse(project, "main.py")
    assert doc.title == "main.py"
    assert "def greet" in doc.content


def test_env_template_file_type(project: Path) -> None:
    doc = parse(project, ".env.example")
    assert doc.file_type == "env"
    assert doc.title == ".env.example"


def test_document_id_is_stable(project: Path) -> None:
    assert parse(project, "main.py").id == parse(project, "main.py").id
    assert parse(project, "main.py").id != parse(project, "README.md").id


@pytest.mark.parametrize("name", ["config.yaml", "data.json"])
def test_valid_structured_files(project: Path, name: str) -> None:
    doc = parse(project, name)
    assert doc.metadata["valid_syntax"] is True
    assert doc.content == (project / name).read_text()  # raw text kept


@pytest.mark.parametrize(("name", "text"), [("bad.json", '{"a": 1,'), ("bad.yaml", "a: [1, 2")])
def test_invalid_structured_files_are_noted_not_fatal(project: Path, name: str, text: str) -> None:
    (project / name).write_text(text)
    doc = parse(project, name)
    assert doc.metadata["valid_syntax"] is False
    assert doc.metadata["syntax_error"]
    assert doc.content == text


def test_yaml_is_not_evaluated(project: Path) -> None:
    (project / "evil.yaml").write_text("!!python/object/apply:os.system ['echo pwned']\n")
    doc = parse(project, "evil.yaml")
    assert doc.metadata["valid_syntax"] is False  # safe_load refuses python tags


def test_utf8_bom_is_stripped(project: Path) -> None:
    (project / "bom.txt").write_bytes("﻿hello".encode())
    doc = parse(project, "bom.txt")
    assert doc.content == "hello"
    assert doc.metadata["decoding_replaced_chars"] is False


def test_invalid_utf8_is_replaced_and_recorded(project: Path) -> None:
    (project / "latin1.txt").write_bytes("café".encode("latin-1"))
    doc = parse(project, "latin1.txt")
    assert doc.content == "caf�"
    assert doc.metadata["decoding_replaced_chars"] is True


def test_pdf_pages_are_extracted(project: Path) -> None:
    make_pdf(project / "doc.pdf", ["First page text", "Second page text"])
    doc = parse(project, "doc.pdf")
    assert doc.file_type == "pdf"
    assert doc.metadata["page_count"] == 2
    pages = doc.metadata["pages"]
    assert [p["page"] for p in pages] == [1, 2]
    for page, expected in zip(pages, ["First page text", "Second page text"]):
        assert expected in doc.content[page["start_char"]:page["end_char"]]


def test_corrupted_pdf_raises_parse_error(project: Path) -> None:
    (project / "broken.pdf").write_bytes(b"%PDF-1.4\x00\x00 garbage")
    with pytest.raises(ParseError):
        parse(project, "broken.pdf")


def test_parse_documents_collects_failures(project: Path) -> None:
    (project / "broken.pdf").write_bytes(b"not a pdf at all")
    paths = [project / "broken.pdf", project / "README.md"]
    docs, skipped = parse_documents(paths, project, "sample_project")
    assert [d.file_path for d in docs] == ["README.md"]
    assert len(skipped) == 1
    assert skipped[0].path == "broken.pdf"
    assert skipped[0].reason == SkipReason.PARSE_ERROR
