# Architecture

This document describes the design of each implemented phase. For the overall
roadmap, see the [README](../README.md).

## Phase 1: Ingestion & chunking

Ingestion turns a repository on disk into a list of chunks: small, overlapping pieces
of text with enough metadata to trace each one back to its source.

```
project root
   │
   ▼
FileLoader.discover()       → accepted paths + SkippedFile records
   │
   ▼
parse_documents()           → Document objects + parse_error skips
   │
   ▼
CharacterChunker.chunk()    → Chunk objects
```

All three steps are tied together by `run_pipeline()` in
[`app/ingestion/pipeline.py`](../app/ingestion/pipeline.py). It returns a
`PipelineResult` with the root, the project name, the number of files found, every
skipped file, the documents and the chunks. The CLI only parses arguments and
prints this result. Core modules never import from the CLI.

### Configuration

[`app/config.py`](../app/config.py) defines a single `Settings` class
(pydantic-settings). Values come from environment variables or `.env`, and list
fields accept JSON arrays. Invalid combinations fail at startup. For example,
`CHUNK_OVERLAP` must satisfy `0 <= overlap < CHUNK_SIZE`.

### File loader

[`file_loader.py`](../app/ingestion/file_loader.py) walks the project root with
`os.walk(followlinks=False)`. It only inspects names, sizes and the first few
kilobytes of a file. It never imports or executes project code and never logs
file contents.

Excluded directories (names or globs such as `*.egg-info`) are removed from the
walk in place, so the walker never descends into `node_modules/` or `.venv/`. Each
file is then checked in this order. The first failing check decides the skip
reason:

| Order | Check | Skip reason |
|---|---|---|
| 1 | Resolved path is inside the root | `outside_root` |
| 2 | File name *or symlink target name* matches a sensitive pattern (unless allow-listed) | `sensitive` |
| 3 | Extension is supported (allow-listed names such as `.env.example` bypass this) | `unsupported_extension` |
| 4 | It is a regular file and can be read | `unreadable` |
| 5 | Size is within `MAX_FILE_SIZE_MB` | `too_large` |
| 6 | No NUL bytes in the first 8 KB (PDFs are exempt) | `binary` |
| – | Excluded directory (recorded once, not per file) | `excluded_dir` |
| – | The parser failed (recorded by the parser stage) | `parse_error` |

Each skip is recorded as a `SkippedFile(path, reason, detail)` and logged at INFO
level, so the CLI can report counts per reason.

#### Security checks

- **Path containment:** `resolve_within_root()` resolves every path, following
  symlinks and `..`, and requires the result to be inside the resolved root.
  This rejects `../secret.txt`, absolute paths elsewhere, and symlinks (to files or
  directories) that point outside the project.
- **Sensitive files:** `.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa*`, `*secret*`,
  `*credentials*` and `*.p12` are matched case-insensitively against the file name
  and the symlink target's name, so `notes.md → .env` is still rejected. Templates
  like `.env.example` are explicitly allow-listed.
- **No execution:** files are only read as bytes. JSON and YAML are validated with
  `json.loads` and `yaml.safe_load_all`, which never construct arbitrary objects.

### Document parser

[`document_parser.py`](../app/ingestion/document_parser.py) turns a path into a
`Document`:

- **Text files** (`.md`, `.txt`, `.py`, `.json`, `.yaml`, `.yml`, env templates) are
  decoded with `utf-8-sig`, which strips a BOM if there is one. If that fails, they
  are decoded as UTF-8 with `errors="replace"`, and the document records
  `decoding_replaced_chars: true`.
- **PDFs** are read page by page with PyMuPDF. Pages are joined with a blank line,
  and `metadata.pages` stores each page's `start_char` and `end_char`, so a chunk
  can be mapped back to page numbers.
- **JSON / YAML** keep their raw text as content. A syntax check stores
  `valid_syntax` and, if it fails, a short `syntax_error` in metadata. An invalid
  config file is still indexed.
- **Markdown** uses the first `# ` heading as the title. Other files use the file name.
- **file_type** is the extension without the dot, or `env` for `.env.*` templates.

Any failure in a single file becomes a `ParseError`. `parse_documents()` turns it
into a `parse_error` skip, so one corrupted file never stops the run.

### Chunker

[`chunker.py`](../app/ingestion/chunker.py) defines a `Chunker` protocol
(`chunk(document) -> list[Chunk]`), so a code-aware chunker (for example, splitting
Python by functions and classes with `ast`) can be added later without touching the
pipeline.

`CharacterChunker(chunk_size, overlap)` is the baseline:

1. Take a window of at most `chunk_size` characters.
2. If the window doesn't reach the end of the text, move its end back to the last
   paragraph break, then line break, then space. Only breaks in the second half of
   the window count. If there is none, cut at the hard limit.
3. The next chunk starts at `max(end - overlap, start + 1)`. The `start + 1` part
   guarantees forward progress even when a short break plus a large overlap would
   otherwise step backwards.
4. A safety guard caps the loop at `len(text) + 1` iterations and raises
   `RuntimeError` if it is exceeded, so a bug in the progress rule fails loudly
   instead of hanging.
5. Whitespace-only chunks are dropped.

Every chunk stores `start_char` and `end_char`, so
`document.content[start:end] == chunk.content`. Chunk metadata includes the
document title and, for PDFs, the page numbers the chunk overlaps.

### Identifiers

| ID | Formula | Property |
|---|---|---|
| `Document.id` | `sha256(project_name + ":" + file_path)[:32]` | Stable across runs, even when the content changes |
| `Chunk.chunk_id` | `sha256(file_path ␀ chunk_index ␀ content)` | Same input gives the same ID; changes when the content changes |

`file_path` is always relative to the project root and uses forward slashes, so
IDs don't depend on where the repository is checked out.
