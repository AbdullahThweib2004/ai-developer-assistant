# AI Developer Assistant

An AI developer assistant that combines **Retrieval-Augmented Generation (RAG)** with the
**Model Context Protocol (MCP)** to help engineers understand codebases. It indexes a
repository's code and documentation, answers questions using the retrieved code as
cited context, and helps investigate errors. Through an MCP server it works with GitHub
issues and pull requests, and any write action needs explicit human approval.

> **Status:** Phases 1–2 are complete: repositories can be ingested, embedded into
> Chroma and searched semantically. Phase 3 (RAG question answering) is next.

## Key features (planned)

- **Codebase understanding**: ask questions about a repository and get answers grounded in its code, with file references.
- **Documentation search**: semantic search across Markdown, text, config files and PDFs.
- **Error investigation**: paste a stack trace and get the relevant code paths and likely causes.
- **GitHub workflows**: read issues and pull requests through an MCP server; draft new issues for human review.
- **Safe by design**: read-only by default, no code execution, secrets filtered out before indexing.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Ingestion & chunking | ✅ Done |
| 2 | Embeddings & vector search | ✅ Done |
| 3 | RAG question answering | Planned |
| 4 | MCP server | Planned |
| 5 | GitHub integration | Planned |
| 6 | RAG + MCP orchestration | Planned |
| 7 | Human-approved issue creation | Planned |
| 8 | Evaluation, security & documentation | Planned |

## Architecture

A repository goes through an ingestion pipeline that discovers safe files, parses them
and splits them into chunks. The chunks are embedded and stored in Chroma. At question
time the retriever finds the most relevant chunks, and the LLM answers using only that
context. An orchestration layer decides when to use retrieval and when to call GitHub
tools, which are exposed by an MCP server.

```mermaid
flowchart LR
    repo[(Repository)] --> ingest[Ingestion<br/>loader + parser]
    ingest --> chunk[Chunking]
    chunk --> embed[Embeddings]
    embed --> chroma[(Chroma)]
    chroma --> retriever[Retriever]
    retriever --> llm[LLM]

    user([Developer]) --> orch[Orchestration]
    orch --> retriever
    llm --> orch
    orch <--> mcp[MCP server]
    mcp <--> gh[GitHub API]
```

See [docs/architecture.md](docs/architecture.md) for the detailed design.

## Tech stack

- **Python 3.11+**, managed with [uv](https://docs.astral.sh/uv/)
- **pydantic / pydantic-settings**: data models and configuration
- **PyMuPDF**: PDF text extraction
- **PyYAML**: safe YAML validation
- **sentence-transformers** (`all-MiniLM-L6-v2`): local embeddings
- **Chroma**: persistent vector store (cosine similarity)
- **pytest**: tests
- Planned: an LLM API, the **MCP** Python SDK, **Streamlit** (UI)

## Getting started

### Setup

```bash
# Install uv (pick one)
curl -LsSf https://astral.sh/uv/install.sh | sh   # official installer
sudo pacman -S uv                                   # Arch Linux
brew install uv                                     # macOS

# Clone and install the locked dependencies
git clone https://github.com/AbdullahThweib2004/ai-developer-assistant.git
cd ai-developer-assistant
uv sync
```

### Configuration

Settings are read from environment variables or a `.env` file. Start from the template:

```bash
cp .env.example .env
```

| Variable | Default | Meaning |
|---|---|---|
| `PROJECT_ROOT` | `.` | Repository to index (`--path` overrides it) |
| `CHUNK_SIZE` | `1000` | Max characters per chunk |
| `CHUNK_OVERLAP` | `150` | Characters shared by consecutive chunks (must be `< CHUNK_SIZE`) |
| `MAX_FILE_SIZE_MB` | `2` | Larger files are skipped |
| `SUPPORTED_EXTENSIONS` | `.md .txt .py .json .yaml .yml .pdf` | JSON array to override |
| `EXCLUDED_DIRS` | `.git`, `.venv`, `node_modules`, `*.egg-info`, ... | Directory names or globs |
| `EXCLUDED_FILE_PATTERNS` | `.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa*`, `*secret*`, `*credentials*`, `*.p12` | Sensitive files that are never read |
| `EMBEDDING_PROVIDER` | `sentence_transformers` | Embedding backend (`fake` is for tests only) |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Model name; stored with the index |
| `EMBEDDING_BATCH_SIZE` | `32` | Chunks embedded per batch |
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Where the vector index is stored (gitignored) |
| `CHROMA_COLLECTION_PREFIX` | `project` | Collections are named `<prefix>_<folder_name>_<path_hash>` |
| `RETRIEVAL_TOP_K` | `5` | Default number of search results |
| `RETRIEVAL_MIN_SCORE` | `0.0` | Minimum similarity (0–1) for a result |

### Run the CLI

**Ingestion** (no embeddings, quick to run):

```bash
# Ingest a repository and print a summary
uv run python -m app.cli ingest --path ./some/repo

# Write every chunk to a JSONL file
uv run python -m app.cli ingest --path ./some/repo --output chunks.jsonl

# Show why each file was skipped
uv run python -m app.cli ingest --path ./some/repo -v
```

Example output:

```
Files found (eligible):  5
Skipped:                 3
  - excluded_dir           2
  - sensitive              1
Documents parsed:        5
Total chunks:            5
  - env                    1
  - json                   1
  - md                     1
  - py                     1
  - yaml                   1
```

**Indexing and search** (the embedding model is downloaded on first use):

```bash
# Embed a repository into Chroma (use --rebuild to replace an existing index)
uv run python -m app.cli index --path ./some/repo --rebuild

# Semantic search: rank, score, file, chunk and a preview
uv run python -m app.cli search --path ./some/repo "How are passwords verified?" --top-k 5
uv run python -m app.cli search --path ./some/repo "database settings" --type yaml

# What is indexed for a repository
uv run python -m app.cli stats --path ./some/repo
```

> **Upgrading from an earlier version:** collection names now include a hash of the
> repository's absolute path (`project_<folder>_<hash8>`). Indexes created before
> this change are not found under the new name. Re-run `index --path <repo> --rebuild`
> once per repository, then optionally delete the old `chroma_db/` directory.

Example search output:

```
1. [0.621] app/ingestion/chunker.py (chunk 2)
   his means the progress rule is broken. max_iterations = length + 1 while start < length: ...
```

### Run the tests

```bash
uv run pytest            # fast suite: fake embeddings, temp Chroma dirs, no network
uv run pytest -m slow    # also downloads and tests the real embedding model
```

### Evaluate retrieval

```bash
uv run python evaluation/evaluate_retrieval.py
```

This indexes `tests/fixtures/sample_project` with the configured model and runs the
questions in [`evaluation/questions.json`](evaluation/questions.json). It reports
file-level Precision@5, Recall@5 and MRR and saves the results to
`evaluation/results/` (gitignored).

**Baseline** (`all-MiniLM-L6-v2`, chunk size 1000 / overlap 150, 12 questions, 8 files):

| Metric | Score |
|---|---|
| Precision@5 | 0.250 |
| Recall@5 | 1.000 |
| MRR | 0.958 |

Each question has only 1–2 relevant files, so Precision@5 can't go above 0.2–0.4.
The fixture is small, so these numbers are a regression baseline, not a measure of
real-world quality.

## Project structure

```
ai-developer-assistant/
├── app/
│   ├── config.py               # settings (pydantic-settings, .env)
│   ├── cli.py                  # command-line interface
│   ├── ingestion/
│   │   ├── models.py           # Document, Chunk, SkippedFile, SkipReason
│   │   ├── file_loader.py      # safe file discovery
│   │   ├── document_parser.py  # text / PDF / JSON / YAML parsing
│   │   ├── chunker.py          # Chunker protocol + CharacterChunker
│   │   └── pipeline.py         # discover -> parse -> chunk
│   └── retrieval/
│       ├── embeddings.py       # EmbeddingProvider protocol, sentence-transformers, fake
│       ├── vector_store.py     # Chroma wrapper (one collection per project)
│       ├── indexer.py          # pipeline -> embeddings -> Chroma
│       └── retriever.py        # query -> ranked chunks
├── evaluation/
│   ├── questions.json          # retrieval questions with relevant files
│   └── evaluate_retrieval.py   # Precision@k, Recall@k, MRR
├── tests/
│   ├── fixtures/sample_project/  # small fake repository used by tests
│   └── test_*.py
├── docs/
│   └── architecture.md
├── .env.example
├── pyproject.toml
└── uv.lock
```

## Security principles

- **Read-only by default.** The assistant reads repositories and GitHub data. It does not modify them unless a write action is explicitly approved.
- **No code execution.** Indexed project code is never imported, executed or evaluated. YAML and JSON are only validated with safe parsers.
- **Secrets never leave the machine.** `.env` files, keys, certificates and credential files are skipped before they are read, and are never logged or indexed. Secrets are never committed to this repository.
- **Human approval for write actions.** Creating issues, comments or pull requests always needs explicit confirmation from the user.
- **Retrieved content is untrusted.** Text from indexed files or GitHub is treated as data, never as instructions, to guard against prompt injection.
- **Respects `.gitignore`.** Files ignored by the repository's `.gitignore` files (root and nested, including `!` negations) are not indexed. Built-in exclusions still apply to repositories without a `.gitignore`.
- **Path containment.** Every file path is resolved and must stay inside the project root, so `../` paths and symlinks pointing outside are rejected.

## License

Released under the [MIT License](LICENSE).
