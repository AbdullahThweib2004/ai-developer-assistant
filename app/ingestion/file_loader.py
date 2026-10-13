"""Discover which files in a project should be ingested.

The loader only *looks* at files (names, sizes, a few leading bytes) and reads
the project's .gitignore and .assistantignore files. It never executes or
imports project code, and never logs file contents.

``check_dir`` / ``check_file`` hold every rule; ``discover`` (ingestion) and
``check_path`` (single-path access, e.g. the MCP server) both go through them,
so there is exactly one definition of which files are allowed.
"""

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import GitIgnoreSpec

from app.config import Settings
from app.ingestion.models import SkippedFile, SkipReason

logger = logging.getLogger(__name__)

BINARY_SNIFF_BYTES = 8192
# Formats that are legitimately binary; the parser decides whether they are valid.
BINARY_FORMATS = {".pdf"}
GITIGNORE = ".gitignore"
# Optional tool-specific ignore file at the project root (gitignore syntax).
ASSISTANTIGNORE = ".assistantignore"

Check = tuple[SkipReason | None, str]


@dataclass
class LoadResult:
    """Output of a discovery run."""

    root: Path
    files: list[Path] = field(default_factory=list)  # absolute, resolved, inside root
    skipped: list[SkippedFile] = field(default_factory=list)


class FileLoader:
    """Walks a project root and selects files that are safe and supported."""

    def __init__(self, settings: Settings, root: Path | None = None) -> None:
        self.settings = settings
        self.root = (root or settings.project_root).resolve()
        self._excluded_dirs = settings.excluded_dirs
        self._extensions = {ext.lower() for ext in settings.supported_extensions}
        self._sensitive_patterns = [p.lower() for p in settings.excluded_file_patterns]
        self._allowed_names = {n.lower() for n in settings.allowed_file_names}
        self._gitignore_specs: dict[Path, GitIgnoreSpec | None] = {}
        self._assistantignore_spec: GitIgnoreSpec | None = None
        self._assistantignore_loaded = False

    # ---- public API -----------------------------------------------------

    def discover(self) -> LoadResult:
        """Walk the root and return accepted files plus everything skipped."""
        if not self.root.is_dir():
            raise NotADirectoryError(f"Project root is not a directory: {self.root}")

        result = LoadResult(root=self.root)
        # followlinks=False: symlinked directories are never descended into.
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            current = Path(dirpath)
            self._prune_dirs(current, dirnames, result)
            for name in sorted(filenames):
                path = current / name
                reason, detail = self.check_file(path)
                if reason is None:
                    result.files.append(path.resolve())
                else:
                    self._skip(result, path, reason, detail)
        return result

    def resolve_within_root(self, path: Path | str) -> Path | None:
        """Resolve ``path`` (relative paths are relative to root).

        Returns the resolved path, or ``None`` if it lies outside the root,
        e.g. ``../secrets.txt`` or a symlink pointing elsewhere.
        """
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):  # e.g. symlink loops
            return None
        return resolved if resolved.is_relative_to(self.root) else None

    def check_path(self, path: Path | str) -> Check:
        """Check one file path from outside the walk (e.g. an MCP request).

        ``path`` may be relative to the root or absolute. Applies the same
        rules as ``discover``: every directory between the root and the file
        must pass ``check_dir``, then the file must pass ``check_file``.
        """
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        # Lexical normalization first, so "a/../../x" is judged by where it points.
        normalized = Path(os.path.normpath(candidate))
        if not normalized.is_relative_to(self.root) or normalized == self.root:
            return SkipReason.OUTSIDE_ROOT, "path is outside the project root"
        parts = normalized.relative_to(self.root).parts
        for depth in range(1, len(parts)):
            directory = self.root.joinpath(*parts[:depth])
            if directory.is_symlink():
                if self.resolve_within_root(directory) is None:
                    return SkipReason.OUTSIDE_ROOT, "path goes through a symlink pointing outside the root"
                return SkipReason.UNREADABLE, "path goes through a symlinked directory (not indexed)"
            reason, detail = self.check_dir(directory)
            if reason is not None:
                return reason, detail
        if not normalized.exists() and not normalized.is_symlink():
            return SkipReason.UNREADABLE, "file does not exist"
        return self.check_file(normalized)

    def check_dir(self, path: Path) -> Check:
        """Decide whether the walk may descend into directory ``path``."""
        if any(fnmatch.fnmatchcase(path.name, pattern) for pattern in self._excluded_dirs):
            return SkipReason.EXCLUDED_DIR, "excluded directory"
        if path.is_symlink() and self.resolve_within_root(path) is None:
            return SkipReason.OUTSIDE_ROOT, "symlinked dir points outside root"
        if self.is_gitignored(path, is_dir=True):
            return SkipReason.GITIGNORED, "directory matched by .gitignore"
        if self.is_assistantignored(path, is_dir=True):
            return SkipReason.ASSISTANTIGNORED, "directory matched by .assistantignore"
        return None, ""

    def check_file(self, path: Path) -> Check:
        """Decide whether a single file should be ingested.

        Assumes its parent directories already passed ``check_dir`` (true
        during ``discover``; ``check_path`` verifies it for single paths).
        Returns ``(None, "")`` if accepted, otherwise ``(reason, detail)``.
        """
        resolved = self.resolve_within_root(path)
        if resolved is None:
            return SkipReason.OUTSIDE_ROOT, "resolves outside project root"
        # Check the target name too, so a symlink can't disguise a secret file.
        if self.is_sensitive(path.name) or self.is_sensitive(resolved.name):
            return SkipReason.SENSITIVE, "matches a sensitive file pattern"
        if self.is_gitignored(path):
            return SkipReason.GITIGNORED, "matched by .gitignore"
        if self.is_assistantignored(path):
            return SkipReason.ASSISTANTIGNORED, "matched by .assistantignore"

        suffix = path.suffix.lower()
        # Allow-listed names (e.g. .env.example) are indexed as plain text.
        if suffix not in self._extensions and path.name.lower() not in self._allowed_names:
            return SkipReason.UNSUPPORTED_EXTENSION, suffix or "(no extension)"

        try:
            if not resolved.is_file():
                return SkipReason.UNREADABLE, "not a regular file"
            size = resolved.stat().st_size
            if size > self.settings.max_file_size_bytes:
                return SkipReason.TOO_LARGE, f"{size} bytes"
            if suffix not in BINARY_FORMATS and self._looks_binary(resolved):
                return SkipReason.BINARY, "contains NUL bytes"
        except OSError as exc:
            return SkipReason.UNREADABLE, type(exc).__name__

        return None, ""

    def is_sensitive(self, file_name: str) -> bool:
        """True if the file name matches a sensitive pattern (and isn't allow-listed)."""
        name = file_name.lower()
        if name in self._allowed_names:
            return False
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self._sensitive_patterns)

    def is_gitignored(self, path: Path, is_dir: bool = False) -> bool:
        """True if the project's .gitignore files (root and nested) ignore ``path``.

        Like git, a .gitignore in a deeper directory overrides shallower ones,
        and within one file the last matching pattern wins (so ``!keep.md``
        re-includes a file). Only .gitignore files inside the root are used.
        """
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.root) or path == self.root:
            return False
        parts = path.relative_to(self.root).parts
        # Ancestor directories from the root down to the path's parent.
        ancestors = [self.root.joinpath(*parts[:depth]) for depth in range(len(parts))]
        ignored = False
        for directory in ancestors:
            spec = self._gitignore_spec(directory)
            if spec is None:
                continue
            rel = path.relative_to(directory).as_posix() + ("/" if is_dir else "")
            match = spec.check_file(rel).include
            if match is not None:
                ignored = match
        return ignored

    def is_assistantignored(self, path: Path, is_dir: bool = False) -> bool:
        """True if the root ``.assistantignore`` ignores ``path`` (gitignore syntax)."""
        spec = self._assistantignore()
        path = Path(os.path.abspath(path))
        if spec is None or not path.is_relative_to(self.root) or path == self.root:
            return False
        rel = path.relative_to(self.root).as_posix() + ("/" if is_dir else "")
        return bool(spec.check_file(rel).include)

    def relative_path(self, path: Path) -> str:
        """Path relative to the root, POSIX style (used as a stable identifier)."""
        return path.relative_to(self.root).as_posix()

    # ---- helpers --------------------------------------------------------

    def _prune_dirs(self, current: Path, dirnames: list[str], result: LoadResult) -> None:
        """Remove excluded / unsafe directories from ``dirnames`` in place."""
        keep: list[str] = []
        for name in sorted(dirnames):
            path = current / name
            reason, detail = self.check_dir(path)
            if reason is None:
                keep.append(name)
            else:
                self._skip(result, path, reason, detail)
        dirnames[:] = keep

    def _skip(self, result: LoadResult, path: Path, reason: SkipReason, detail: str) -> None:
        rel = self.relative_path(path)
        logger.info("Skipping %s: %s (%s)", rel, reason.value, detail)
        result.skipped.append(SkippedFile(path=rel, reason=reason, detail=detail))

    def _gitignore_spec(self, directory: Path) -> GitIgnoreSpec | None:
        """Parsed .gitignore of ``directory`` (cached), or None if there is none."""
        if directory not in self._gitignore_specs:
            self._gitignore_specs[directory] = self._load_ignore_file(directory / GITIGNORE)
        return self._gitignore_specs[directory]

    def _assistantignore(self) -> GitIgnoreSpec | None:
        if not self._assistantignore_loaded:
            self._assistantignore_spec = self._load_ignore_file(self.root / ASSISTANTIGNORE)
            self._assistantignore_loaded = True
        return self._assistantignore_spec

    def _load_ignore_file(self, file: Path) -> GitIgnoreSpec | None:
        """Parse an ignore file inside the root; None if missing or unreadable."""
        resolved = self.resolve_within_root(file)
        if resolved is None or not resolved.is_file():
            return None
        try:
            if resolved.stat().st_size > self.settings.max_file_size_bytes:
                return None
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
            return GitIgnoreSpec.from_lines(lines)
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable %s: %s", self.relative_path(file), type(exc).__name__)
            return None

    @staticmethod
    def _looks_binary(path: Path) -> bool:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(BINARY_SNIFF_BYTES)
