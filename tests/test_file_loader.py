"""Tests for file discovery and its security rules."""

from pathlib import Path

import pytest

from app.ingestion.file_loader import FileLoader
from app.ingestion.models import SkipReason
from tests.helpers import FIXTURE_FILES, make_settings


def discover(root: Path, **overrides: object):
    loader = FileLoader(make_settings(root, **overrides))
    result = loader.discover()
    accepted = {loader.relative_path(p) for p in result.files}
    skipped = {s.path: s.reason for s in result.skipped}
    return accepted, skipped


def test_accepts_supported_files(project: Path) -> None:
    accepted, _ = discover(project)
    assert accepted == FIXTURE_FILES


def test_skips_excluded_dirs_without_descending(project: Path) -> None:
    accepted, skipped = discover(project)
    assert skipped["node_modules"] == SkipReason.EXCLUDED_DIR
    assert skipped[".venv"] == SkipReason.EXCLUDED_DIR
    assert not any(p.startswith(("node_modules/", ".venv/")) for p in accepted | skipped.keys())


def test_excluded_dir_globs(project: Path) -> None:
    (project / "pkg.egg-info").mkdir()
    (project / "pkg.egg-info" / "SOURCES.txt").write_text("main.py")
    accepted, skipped = discover(project)
    assert skipped["pkg.egg-info"] == SkipReason.EXCLUDED_DIR
    assert "pkg.egg-info/SOURCES.txt" not in accepted


def test_env_is_sensitive_but_env_example_is_allowed(project: Path) -> None:
    accepted, skipped = discover(project)
    assert skipped[".env"] == SkipReason.SENSITIVE
    assert ".env.example" in accepted


@pytest.mark.parametrize(
    "name", [".env", ".env.local", "server.pem", "api.key", "id_rsa", "id_rsa.pub",
             "my_secret.txt", "AWS_Credentials.json", "cert.p12"],
)
def test_sensitive_patterns(project: Path, name: str) -> None:
    assert FileLoader(make_settings(project)).is_sensitive(name)


def test_unsupported_and_too_large_and_binary(project: Path) -> None:
    (project / "image.png").write_bytes(b"\x89PNG")
    (project / "big.txt").write_text("x" * 2048)
    (project / "blob.txt").write_bytes(b"abc\x00def")
    _, skipped = discover(project, max_file_size_mb=0.001)  # ~1 KB
    assert skipped["image.png"] == SkipReason.UNSUPPORTED_EXTENSION
    assert skipped["big.txt"] == SkipReason.TOO_LARGE
    assert skipped["blob.txt"] == SkipReason.BINARY


def test_pdf_is_not_rejected_by_binary_check(project: Path) -> None:
    (project / "doc.pdf").write_bytes(b"%PDF-1.4\x00\x00 not really a pdf")
    accepted, _ = discover(project)
    assert "doc.pdf" in accepted  # the parser decides whether it's valid


@pytest.mark.parametrize("path", ["../outside.txt", "../../etc/passwd", "/etc/passwd", "a/../../x.md"])
def test_resolve_within_root_rejects_traversal(project: Path, path: str) -> None:
    assert FileLoader(make_settings(project)).resolve_within_root(path) is None


def test_resolve_within_root_accepts_inside_paths(project: Path) -> None:
    loader = FileLoader(make_settings(project))
    assert loader.resolve_within_root("README.md") == (project / "README.md").resolve()
    assert loader.resolve_within_root("sub/../main.py") == (project / "main.py").resolve()


def test_symlinks_pointing_outside_root_are_skipped(project: Path, tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "leak.md").write_text("# private")
    (project / "link.md").symlink_to(outside_dir / "leak.md")
    (project / "linked_dir").symlink_to(outside_dir, target_is_directory=True)

    accepted, skipped = discover(project)
    assert skipped["link.md"] == SkipReason.OUTSIDE_ROOT
    assert skipped["linked_dir"] == SkipReason.OUTSIDE_ROOT
    assert not any("leak" in p for p in accepted)


def test_symlink_disguising_secret_is_skipped(project: Path) -> None:
    (project / "notes.md").symlink_to(project / ".env")
    _, skipped = discover(project)
    assert skipped["notes.md"] == SkipReason.SENSITIVE


def test_root_must_be_a_directory(project: Path) -> None:
    with pytest.raises(NotADirectoryError):
        discover(project / "README.md")
