# Changelog

## v1.0.0 (2026-10-15)

First complete release: all eight phases done.

- **Ingestion and retrieval:** safe file discovery (`.gitignore`, `.assistantignore`, secret
  and binary filtering), parsing (code, Markdown, config, PDF), chunking, local embeddings
  (`all-MiniLM-L6-v2`) and Chroma vector search.
- **RAG answers:** cited answers with validated `[S#]` citations and an "insufficient
  evidence" path; Anthropic, OpenAI and OpenAI-compatible providers (OpenRouter, Groq, Ollama).
- **MCP server:** read-only project and GitHub tools, sandboxed to allowed roots.
- **GitHub (read-only):** GET-only client with an allowlist, rate-limit handling and
  untrusted-content wrapping.
- **Orchestration:** a bounded, traced tool-use loop that decides between project, GitHub,
  both or neither; CLI `investigate` and `chat`.
- **Human-approved issue creation:** the model only drafts; creation happens after typing the
  repository name, with single-use hash-bound approvals, a separate write token and
  allowlist, and an audit log.
- **Web app:** FastAPI + React UI (assistant, Investigation Context, Code Explorer, Knowledge
  Base, GitHub issues and pull requests, Activity, Settings, Ctrl/Cmd+K search), local-only
  with a per-launch token, and a `--demo` mode that needs no keys.
- **Evaluation and security:** adversarial prompt-injection cases (0 forbidden calls, 0
  leaks), orchestration and retrieval evaluations, `docs/security.md`, `docs/evaluation.md`.
- **CI:** ruff, pytest, TypeScript, Vitest and a production build on every push and pull request.
