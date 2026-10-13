# MCP server

The assistant's project-knowledge features are also available as an
[MCP](https://modelcontextprotocol.io) server, so any MCP client can search and read
indexed projects.

## What MCP is

The **Model Context Protocol** is an open protocol that connects LLM applications
("clients" or "hosts", such as an IDE assistant or a desktop chat app) to external
capabilities ("servers"). A server publishes **tools**, each with a name, a
description, a JSON input schema and an optional output schema. The client's model
decides when to call a tool, and the client sends the call over a transport such as
stdio (a local subprocess) or HTTP. This server uses **stdio**: the client starts it
as a subprocess and exchanges JSON-RPC messages over stdin/stdout.

## Tools

All tools are **read-only**. They are annotated `readOnlyHint: true`,
`destructiveHint: false`, `idempotentHint: true` and `openWorldHint: false`. The
server makes no writes, runs no shell commands, executes no code and makes no LLM
calls. Paths in results are always relative to the project root.

| Tool | Purpose |
|---|---|
| `ping` | Health check |
| `get_index_stats` | What is indexed for a project |
| `search_project` | Semantic search over an indexed project |
| `read_project_file` | Read a file with line numbers, optionally a line range |
| `list_project_files` | Files the indexer would include, plus skip counts |

### `ping`

- **Input:** none
- **Output:** `{ server: str, version: str, status: "ok" }`

### `get_index_stats`

- **Input:** `project_path: str` (absolute, inside an allowed root)
- **Output:** `{ project_name, collection_name, chunk_count: int, embedding_model: str | null, files_indexed: int, files: [str] }`
- **Errors:** project outside the allowed roots or not a directory; project not indexed.

### `search_project`

- **Input:**
  - `project_path: str` (absolute)
  - `query: str` (1–2000 chars, not blank)
  - `top_k: int = 5` (1…`MCP_MAX_TOP_K`)
  - `file_types: [str] | null`, e.g. `["py", "md"]`
- **Output:** `{ project_name, query, hits: [SearchHit], note }`, where each
  `SearchHit` is `{ rank, file_path, file_type, chunk_index, score, location, content }`
  and `content` is wrapped in `<untrusted-project-content>` tags.
- **Errors:** project outside the allowed roots; project not indexed; empty query;
  `top_k` out of range; embedding model differs from the index (rebuild needed).
- The embedding model loads on the first search, not when the server starts.

### `read_project_file`

- **Input:**
  - `project_path: str` (absolute)
  - `file_path: str` (relative to the project)
  - `start_line: int | null`, `end_line: int | null` (1-based, inclusive)
- **Output:** `{ file_path, start_line, end_line, total_lines, truncated: bool, content, note }`.
  `content` lines are prefixed `"   12 | "`, and the whole block is wrapped in
  `<untrusted-project-content>` tags. Output is capped at `MCP_MAX_FILE_CHARS`. When
  `truncated` is true, continue from `end_line + 1`.
- **Errors:** `Access to this file is not allowed (<reason>)`, where the reason is one of
  `outside_root`, `sensitive`, `gitignored`, `assistantignored`, `excluded_dir`,
  `unsupported_extension`, `binary`, `too_large`, `unreadable` or `parse_error`.
  Also an invalid line range.

### `list_project_files`

- **Input:** `project_path: str` (absolute), `file_types: [str] | null`
- **Output:** `{ project_name, total_files, files: [{ path, file_type, size_bytes }], truncated, skipped_by_reason: { reason: count } }`
- Skipped files are only **counted**, never named, so the list doesn't reveal
  which secret files exist.
- **Errors:** project outside the allowed roots or not a directory.

The exact JSON schemas are published by the server in `tools/list` (input and
output schemas generated from the Pydantic models in
[`app/mcp_server/schemas.py`](../app/mcp_server/schemas.py)).

## Security model

1. **Allowed roots.** `MCP_ALLOWED_PROJECT_ROOTS` is a comma-separated list of
   absolute paths. The server **refuses to start** without it. Every
   `project_path` is resolved, following symlinks, and must be inside one of these
   roots. Relative project paths are rejected.
2. **The same file rules as ingestion.** `read_project_file` calls
   `FileLoader.check_path`, the function that applies the ingestion rules. Secrets
   (`.env`, `.env.*`, keys, certificates, credentials), gitignored and
   `.assistantignore`d files, excluded directories (`.git`, `node_modules`, …),
   binaries, files over `MAX_FILE_SIZE_MB` and unsupported types are refused.
   `../` traversal, absolute paths outside the project, and symlinks (files or
   directories) pointing outside the project are rejected. A test checks that
   every file ingestion accepts is readable and every file it skips is refused.
3. **Output caps.** File output is limited to `MCP_MAX_FILE_CHARS` (with a
   `truncated` flag), search results to `MCP_MAX_TOP_K`, and file listings to
   1000 entries.
4. **Untrusted content.** Every piece of file or chunk text is wrapped as
   `<untrusted-project-content source="path">…</untrusted-project-content>`, and
   copies of the tag inside the text are escaped so a file can't close the
   wrapper early. Results carry a `note` saying the content may contain
   instructions that must not be followed. The server instructions say the same.
5. **Safe errors.** Tool errors are `ToolError`s with messages written by the
   server. They never contain absolute paths outside the allowed roots (the
   client's `project_path` isn't echoed back), stack traces, SDK messages or
   secrets. Unexpected exceptions become a generic "internal error", and only the
   exception type is logged.
6. **No side effects.** There are no write, shell, network or LLM tools. The only
   network access is the one-time download of the embedding model.
7. **stdout is protocol only.** Logs go to stderr. A test runs the server as a
   subprocess and checks that every stdout line is a JSON-RPC message.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MCP_ALLOWED_PROJECT_ROOTS` | *(empty: server won't start)* | Comma-separated absolute paths |
| `MCP_MAX_FILE_CHARS` | `20000` | Max characters returned by `read_project_file` |
| `MCP_MAX_TOP_K` | `10` | Max `top_k` for `search_project` |
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Use an **absolute** path in client configs (the client chooses the working directory) |

The server also reads the other settings (embedding model, chunking, exclusions)
from the environment and from `.env` in its working directory. MCP clients start
servers with a minimal environment, so pass these variables in the client's `env`
block.

## Running

Index a project first; the server doesn't index:

```bash
uv run python -m app.cli index --path /absolute/path/to/project --rebuild
```

Start the server (normally the client does this for you):

```bash
MCP_ALLOWED_PROJECT_ROOTS=/absolute/path/to/projects uv run python -m app.mcp_server
# or the script entry point
MCP_ALLOWED_PROJECT_ROOTS=/absolute/path/to/projects uv run ai-dev-assistant-mcp
```

Smoke test with a real stdio client (lists tools, calls `ping` and `search_project`
on this repository):

```bash
uv run python scripts/mcp_smoke_test.py "How does the chunker avoid infinite loops?"
```

## Connecting clients

In the examples below, replace `/ABSOLUTE/PATH/TO/ai-developer-assistant` with
where you cloned this repository, and `/ABSOLUTE/PATH/TO/projects` with the
directory (or directories) the server may read. `uv --directory` makes the
repository the working directory, so its `.env` is found.

### MCP Inspector

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) needs
Node.js ≥ 22.19. Save this as `mcp-inspector.json` in the repository root (the
filename is gitignored because it contains your local paths):

```json
{
  "mcpServers": {
    "ai-dev-assistant": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/ai-developer-assistant", "run", "python", "-m", "app.mcp_server"],
      "env": {
        "MCP_ALLOWED_PROJECT_ROOTS": "/ABSOLUTE/PATH/TO/projects",
        "CHROMA_PERSIST_DIR": "/ABSOLUTE/PATH/TO/ai-developer-assistant/chroma_db"
      }
    }
  }
}
```

Web UI (opens in your browser; pick the server and press **Connect**):

```bash
npx @modelcontextprotocol/inspector --config mcp-inspector.json
```

CLI mode, for quick checks from a terminal:

```bash
npx @modelcontextprotocol/inspector --cli --config mcp-inspector.json --server ai-dev-assistant \
  --method tools/list

npx @modelcontextprotocol/inspector --cli --config mcp-inspector.json --server ai-dev-assistant \
  --method tools/call --tool-name ping

npx @modelcontextprotocol/inspector --cli --config mcp-inspector.json --server ai-dev-assistant \
  --method tools/call --tool-name search_project \
  --tool-args-json '{"project_path": "/ABSOLUTE/PATH/TO/projects/my-repo", "query": "database connection", "top_k": 3}'

npx @modelcontextprotocol/inspector --cli --config mcp-inspector.json --server ai-dev-assistant \
  --method tools/call --tool-name read_project_file \
  --tool-args-json '{"project_path": "/ABSOLUTE/PATH/TO/projects/my-repo", "file_path": ".env"}'
# -> error: Access to this file is not allowed (sensitive)
```

For repeatable runs, pin the Inspector version (`npx --yes @modelcontextprotocol/inspector@<version>`).

### Claude Desktop

Add the server to `claude_desktop_config.json` (Settings → Developer → Edit Config),
then restart the app:

```json
{
  "mcpServers": {
    "ai-dev-assistant": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/ai-developer-assistant", "run", "python", "-m", "app.mcp_server"],
      "env": {
        "MCP_ALLOWED_PROJECT_ROOTS": "/ABSOLUTE/PATH/TO/projects",
        "CHROMA_PERSIST_DIR": "/ABSOLUTE/PATH/TO/ai-developer-assistant/chroma_db"
      }
    }
  }
}
```

If `uv` isn't found, use its absolute path (`which uv`) as `command`.

### Cursor

Add the same block to `.cursor/mcp.json` in a project (or `~/.cursor/mcp.json` for
all projects):

```json
{
  "mcpServers": {
    "ai-dev-assistant": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/ai-developer-assistant", "run", "python", "-m", "app.mcp_server"],
      "env": {
        "MCP_ALLOWED_PROJECT_ROOTS": "/ABSOLUTE/PATH/TO/projects",
        "CHROMA_PERSIST_DIR": "/ABSOLUTE/PATH/TO/ai-developer-assistant/chroma_db"
      }
    }
  }
}
```

### Troubleshooting

- **"cannot start: MCP_ALLOWED_PROJECT_ROOTS is empty"**: set it in the client's `env` block.
- **"This project is not indexed yet"**: run `index --path <project> --rebuild`, and
  check that `CHROMA_PERSIST_DIR` in the client config points to the same directory
  the CLI used.
- **Server logs**: they go to stderr. Claude Desktop shows them in its MCP log files,
  and the Inspector shows them in its UI.
