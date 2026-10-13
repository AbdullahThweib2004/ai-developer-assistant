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


# ---- .gitignore support ------------------------------------------------------


def test_root_gitignore_skips_files_and_dirs(project: Path) -> None:
    (project / ".gitignore").write_text("*.log.md\ngenerated/\n/local_notes.md\n")
    (project / "debug.log.md").write_text("# log")
    (project / "generated").mkdir()
    (project / "generated" / "out.py").write_text("x = 1")
    (project / "local_notes.md").write_text("# notes")
    (project / "docs" / "local_notes.md").write_text("# not anchored to root")

    accepted, skipped = discover(project)
    assert skipped["debug.log.md"] == SkipReason.GITIGNORED
    assert skipped["generated"] == SkipReason.GITIGNORED
    assert "generated/out.py" not in accepted | skipped.keys()  # never descended
    assert skipped["local_notes.md"] == SkipReason.GITIGNORED
    assert "docs/local_notes.md" in accepted  # "/pattern" only matches at the root
    assert FIXTURE_FILES <= accepted


def test_nested_gitignore_and_negation(project: Path) -> None:
    (project / ".gitignore").write_text("*.md\n!README.md\n")
    pkg = project / "pkg"
    pkg.mkdir()
    (pkg / ".gitignore").write_text("!keep.md\nscratch.py\n")
    (pkg / "keep.md").write_text("# keep")
    (pkg / "drop.md").write_text("# drop")
    (pkg / "scratch.py").write_text("x = 1")
    (pkg / "module.py").write_text("x = 2")

    accepted, skipped = discover(project)
    assert "README.md" in accepted  # negated in the root .gitignore
    assert skipped["docs/troubleshooting.md"] == SkipReason.GITIGNORED
    assert "pkg/keep.md" in accepted  # nested !keep.md overrides the root *.md
    assert skipped["pkg/drop.md"] == SkipReason.GITIGNORED
    assert skipped["pkg/scratch.py"] == SkipReason.GITIGNORED  # nested rule applies in pkg/
    assert "pkg/module.py" in accepted


def test_nested_gitignore_does_not_affect_siblings(project: Path) -> None:
    (project / "pkg").mkdir()
    (project / "pkg" / ".gitignore").write_text("*.py\n")
    (project / "pkg" / "a.py").write_text("x = 1")
    accepted, skipped = discover(project)
    assert skipped["pkg/a.py"] == SkipReason.GITIGNORED
    assert "main.py" in accepted


def test_sensitive_takes_precedence_over_gitignore(project: Path) -> None:
    (project / ".gitignore").write_text(".env\n")
    _, skipped = discover(project)
    assert skipped[".env"] == SkipReason.SENSITIVE


def test_builtin_exclusions_apply_without_gitignore(project: Path) -> None:
    assert not (project / ".gitignore").exists()
    _, skipped = discover(project)
    assert skipped["node_modules"] == SkipReason.EXCLUDED_DIR


# ---- .assistantignore and the shared single-path check ------------------------


def test_assistantignore_skips_files_and_dirs(project: Path) -> None:
    (project / ".assistantignore").write_text("docs/\nmain.py\n*.json\n!data.json\n")
    (project / "extra.json").write_text("{}")
    accepted, skipped = discover(project)
    assert skipped["docs"] == SkipReason.ASSISTANTIGNORED
    assert skipped["main.py"] == SkipReason.ASSISTANTIGNORED
    assert skipped["extra.json"] == SkipReason.ASSISTANTIGNORED
    assert "data.json" in accepted  # negation works
    assert "auth.py" in accepted


def test_assistantignore_is_only_read_at_the_root(project: Path) -> None:
    (project / "docs" / ".assistantignore").write_text("*.md\n")
    accepted, _ = discover(project)
    assert "docs/troubleshooting.md" in accepted


def test_without_assistantignore_nothing_extra_is_skipped(project: Path) -> None:
    accepted, skipped = discover(project)
    assert SkipReason.ASSISTANTIGNORED not in skipped.values()
    assert accepted == FIXTURE_FILES


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("auth.py", None),
        ("docs/troubleshooting.md", None),
        (".env", SkipReason.SENSITIVE),
        ("node_modules/lib/index.md", SkipReason.EXCLUDED_DIR),
        (".venv/lib/site.py", SkipReason.EXCLUDED_DIR),
        ("../outside.md", SkipReason.OUTSIDE_ROOT),
        ("docs/../../outside.md", SkipReason.OUTSIDE_ROOT),
        ("/etc/passwd", SkipReason.OUTSIDE_ROOT),
        ("docs/../.env", SkipReason.SENSITIVE),
        ("missing.py", SkipReason.UNREADABLE),
        ("image.png", SkipReason.UNSUPPORTED_EXTENSION),
    ],
)
def test_check_path_matches_discover_rules(project: Path, path: str, reason: SkipReason | None) -> None:
    (project / "image.png").write_bytes(b"\x89PNG")
    assert FileLoader(make_settings(project)).check_path(path)[0] == reason


def test_check_path_applies_gitignore_and_assistantignore_dirs(project: Path) -> None:
    (project / ".gitignore").write_text("build_out/\n")
    (project / ".assistantignore").write_text("private/\n")
    for directory in ("build_out", "private"):
        (project / directory).mkdir()
        (project / directory / "notes.md").write_text("# hidden")
    loader = FileLoader(make_settings(project))
    assert loader.check_path("build_out/notes.md")[0] == SkipReason.GITIGNORED
    assert loader.check_path("private/notes.md")[0] == SkipReason.ASSISTANTIGNORED


def test_check_path_rejects_symlinked_dirs(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("# private")
    (project / "escape").symlink_to(outside, target_is_directory=True)
    (project / "alias").symlink_to(project / "docs", target_is_directory=True)
    loader = FileLoader(make_settings(project))
    assert loader.check_path("escape/leak.md")[0] == SkipReason.OUTSIDE_ROOT
    assert loader.check_path("alias/troubleshooting.md")[0] == SkipReason.UNREADABLE  # never indexed either


def test_check_path_agrees_with_discover_on_every_file(project: Path) -> None:
    (project / ".gitignore").write_text("*.log.md\n")
    (project / "x.log.md").write_text("# log")
    loader = FileLoader(make_settings(project))
    result = loader.discover()
    for path in result.files:
        assert loader.check_path(loader.relative_path(path)) == (None, "")
    for skipped in result.skipped:
        assert loader.check_path(skipped.path)[0] is not None
