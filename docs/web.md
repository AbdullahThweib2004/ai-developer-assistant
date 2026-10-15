# Web app

A local web app over the existing services. The backend (`app/web/`, FastAPI) adds no
business logic: it calls the same `build_index`, `AnswerGenerator`, `Investigator` and
`IssueApprovalService` as the CLI. The frontend (`frontend/`, React + TypeScript + Vite +
Tailwind CSS) is built to `frontend/dist/` and served by the same process on one port.

```bash
cd frontend && npm install && npm run build && cd ..
uv run ai-dev-assistant web --demo            # fake LLM/embeddings/GitHub, no keys, no network
uv run ai-dev-assistant web                   # real settings from .env
uv run ai-dev-assistant web --port 9000       # default port is 8765
```

If `frontend/dist` is missing, the server still starts and prints the build command;
the page shows it too.

## Local security

The app holds an LLM key and can create GitHub issues, so it is local-only by default:

- **Loopback only.** It binds to `127.0.0.1`. Any other `--host` is refused unless
  `--allow-network` is also passed, which prints a loud warning.
- **Host check.** Requests whose `Host` header isn't `127.0.0.1:<port>` or
  `localhost:<port>` get `421`. This blocks DNS-rebinding attacks.
- **Origin check.** A request with an `Origin` other than the app's own (and, with
  `--dev`, the Vite dev server) gets `403`, as does `Sec-Fetch-Site: cross-site`. No CORS
  headers are ever sent.
- **Session token.** A random token is generated per launch and injected into the
  served page as `<meta name="ai-dev-assistant-token">`. Every `/api` request must send
  it in `X-Session-Token`, otherwise `401`. Restarting the server invalidates old tabs.
- **Paths.** Project paths from the client go through the same
  `MCP_ALLOWED_PROJECT_ROOTS` check as the MCP server. Rejections don't echo the path.
- **Safe errors.** Errors are `{"detail": {"category", "message", "hint"}}` with
  messages written by the app. Unexpected exceptions return
  `"Something went wrong."` and are logged by type only.
- **Headers.** `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY`. The API docs (`/docs`, `/openapi.json`) are disabled.
- `/api/status` reports provider, model and feature flags, never keys or tokens.

## API

All routes are under `/api` and need the session token.

| Method | Path | Purpose |
|---|---|---|
| GET | `/status` | provider/model, embedding model, GitHub read/write config, limits, demo flag |
| GET | `/projects` | indexed projects under the allowed roots, with chunk and file counts |
| POST | `/index` | `{path}`: rebuild the index (SSE) |
| POST | `/ask` | `{conversation_id, path, question, top_k?, file_types?}`: one-shot RAG answer (SSE) |
| GET | `/sessions/{id}` | a conversation's live session (tools, memory size) |
| POST | `/investigate` | `{conversation_id, path, question, mode?}`: run the tool loop (SSE) |
| POST | `/sessions/{id}/drafts` | `{turn_id, repo?}`: draft an issue from an answer |
| GET / PUT | `/drafts/{id}` | read, or edit `{title, body, labels}` (new hash, voids approvals) |
| GET | `/drafts/{id}/similar` | similar open issues in the destination repo |
| POST | `/drafts/{id}/approve` | `{draft_hash, typed_repo_confirmation, confirm_similar}` |
| POST | `/drafts/{id}/cancel` | cancel (audited) |

### Conversations

Every conversation belongs to exactly one project (resolved path) and keeps its GitHub
repository and mode. They are stored in `DATA_DIR/conversations.db` (SQLite, file mode 600):
the question, the structured answer (sections, sources, trace metadata, tokens) and the
outcome of each turn, with secrets redacted. Approvals and drafts are never stored.

| Method | Path | Purpose |
|---|---|---|
| GET | `/conversations?path=` | conversations of one project, newest first |
| POST | `/conversations` | `{path, repo, mode}`: create |
| GET | `/conversations/{id}` | with its messages |
| PATCH | `/conversations/{id}` | `{title?, mode?}`: rename, change mode |
| DELETE | `/conversations/{id}` | delete one |
| DELETE | `/conversations?path=` | clear a project's conversations |
| DELETE | `/history` | clear all |
| GET / PUT | `/history/settings` | `{save_history}` (off: new chats stay in memory) |

`/ask` and `/investigate` take `{conversation_id, path, question}` (`/investigate` also an
optional one-off `mode`). The server refuses (`409 conversation_mismatch`) when the
conversation belongs to another project. A conversation's live session (MCP server process,
memory) is created on first use; after a restart its memory is rebuilt from the stored turns,
labeled as previous conversation (context, not evidence), and new questions still retrieve
fresh evidence.

Read-only routes for the explore pages (`app/web/explore.py`). They reuse `ProjectTools` and
`GitHubTools`, so the MCP project allowlist, the ingestion file rules and the GitHub read
allowlist all apply. Nothing here writes.

| Method | Path | Purpose |
|---|---|---|
| GET | `/project/info?path=` | name, path relative to its allowed root, git branch (read from `.git/HEAD`, no git process) |
| GET | `/project/files?path=` | files the loader allows, marked indexed or not; skipped files as counts only |
| GET | `/project/file?path=&file=&start=` | read-only file view via `read_project_file` (same limits and refusals) |
| GET | `/project/search?path=&q=` | semantic search over the index (no LLM), hits with line ranges |
| GET | `/github/status?repo=` | configured / reachable, read access type, rate limit (cached 60 s) |
| GET | `/github/issues?repo=&q=&state=` | search (with `q`) or recent issues |
| GET | `/github/issues/{n}?repo=` | issue with the first and latest comments |
| GET | `/github/pulls?repo=&state=` / `/github/pulls/{n}` | pull requests, with changed files |
| GET | `/activity/audit` | issue-creation audit entries: action, repo, result, short draft hash. Never bodies, titles or tokens |

### Line ranges

Every project source has `start_line` and `end_line`. The index stores character
offsets; the server converts them to lines by reading the file through the same
`FileLoader.check_path` rules (a refused file gets no range). Chunks can start mid-line:
for display, the partial first line is dropped and `start_line` moves to the next line,
so the excerpt always starts at a whole line.

A session runs one investigation at a time (`409 busy` otherwise). Sessions keep the
conversation memory, the answers (`turn_id`) and their own `IssueApprovalService`.

### Server-sent events

Streaming routes are `POST`, so the frontend reads them with `fetch` and a
`ReadableStream` (an `EventSource` can only `GET`). Each event is
`event: <name>\ndata: <json>\n\n`; a `: ping` comment is sent every 15 s when idle. If
the client disconnects (the **Stop** button aborts the request), the work is cancelled
at the next step.

| Route | Events |
|---|---|
| `/index` | `started`, `progress` (`discovered`, `parsed`, `chunked`, `embedding` with `done/total`), `done`, `error` |
| `/ask` | `status`, `answer`, `done`, `error` |
| `/investigate` | `started`, `llm_call`, `step_started`, `step_finished`, `answer`, `done`, `error`, `cancelled` |

`step_started` carries the iteration, tool name, sanitized arguments and a `call_id`;
`step_finished` adds status, duration, result count and error category. `llm_call`
carries token counts and timing. They come from an optional event hook on
`Investigator.ask(question, on_event=...)`; without the hook the orchestrator behaves
exactly as before. Events contain metadata only, never file or issue content, until
the final `answer`.

The `answer` event has the answer text, its sections, the ranked possible causes
(`evidence` / `hypothesis`), project and GitHub sources (with line numbers, excerpts and
whether they were cited), invalid citations, the trace and totals.

## Issue approval through the API

The Phase 7 guarantees are enforced on the server, not by the UI:

1. The LLM has no route to approval. `prepare_github_issue` only drafts; there is no
   approve tool, and the approve route is only called by the drawer's button.
2. `approve` sends the `draft_hash` the user reviewed. If the draft changed since, the
   server answers `409 draft_changed` and creates nothing.
3. The typed repository name is checked by `IssueApprovalService.approve` exactly as in
   the CLI. It produces a single-use, short-lived approval bound to the hash.
4. `submit` consumes the draft's only attempt before the POST. Similar open issues need
   `confirm_similar: true`. Secrets in the draft block approval.
5. Editing a draft (`PUT`) voids earlier approvals. The UI also clears the typed
   confirmation on any edit and disables **Approve** while edits are unsaved.
6. If writing is disabled or not configured, drafting returns `400 write_disabled` with
   instructions, and nothing else changes.
7. The UI shows "Issue #N created" only for a confirmed `201`. Unknown outcomes and
   failures are shown as such, with possible matches.

## Demo mode

`--demo` builds the runtime from `app/web/demo.py`: fake hashed embeddings, a temp
Chroma directory, a scripted LLM that investigates `examples/fastapi-auth-demo` (search,
read two files, search GitHub, answer with valid citations, with small delays so the
steps are visible), and a mocked GitHub API. Writes go to a mock `demo-org/sandbox`.
No key is read and no request leaves the machine.

## Frontend

The UI follows a light-first design: a lavender canvas with the app as one white
rounded container. It has a sidebar (navigation, the current project's Conversations with
New chat / rename / delete, and a Workspace card with the real index status and live progress), a top bar (project selector, read-only git branch, the
Ctrl/Cmd+K search and a GitHub status pill that also picks the repository) and the
pages. Routing is client-side over the History API; the server serves `index.html` for
every page path. A conversation has its own URL (`/chat/:id`), so a reload or a later
visit restores it; the last active conversation per project is remembered (localStorage).
Switching project stops a running request and shows that project's last conversation, or
an empty one; switching back restores the previous conversation exactly as it was.

| Page | Path | Data |
|---|---|---|
| AI Assistant | `/` | `/investigate`, `/ask` streams; Investigation Context from the live events |
| Overview | `/overview` | `/projects`, `/status`, `/github/status`, this session's answers |
| Knowledge Base | `/knowledge` | `/projects`, `/project/files`, `/index` |
| Code Explorer | `/code?file=&start=&end=` | `/project/files`, `/project/file` |
| GitHub Issues | `/issues?number=` | `/github/issues` |
| Pull Requests | `/pulls?number=` | `/github/pulls` |
| Activity | `/activity` | this session's traces, `/activity/audit` |
| Settings | `/settings` | `/status`, `/github/status` |

Example questions in the empty state come from the selected project: the 401/JWT
questions only for the demo project; otherwise questions built from its real file
names (its main modules, its config file, its README).

```
frontend/src/
├── App.tsx                 # shell: sidebar, top bar, page, context panel, drawer, palette
├── index.css               # design tokens (CSS variables), Tailwind theme, code theme
├── lib/
│   ├── store.tsx           # app state + actions (APP_NAME lives here)
│   ├── router.ts           # History API routing
│   ├── api.ts              # fetch + SSE client
│   └── examples.ts, format.ts, lines.ts, citations.ts, highlight.ts, theme.ts, types.ts
├── pages/                  # AssistantPage, CodeExplorerPage, GitHubPages, InfoPages
└── components/             # NavSidebar, TopBar, Select, CommandPalette, ContextPanel,
                            # MessageView, AnswerView, SourceCards, StepTimeline, CitationChip,
                            # DraftDrawer, Trace, FileIcon, Toasts, ui
```

All dropdowns use one custom `Select` (a listbox with keyboard support), never a
native `<select>`.

Development with hot reload:

```bash
uv run ai-dev-assistant web --dev --demo   # writes frontend/.env.development.local (gitignored)
cd frontend && npm run dev                 # http://localhost:5173, proxies /api to the backend
```

`--dev` allows the Vite origin and writes the session token for Vite to read.

Tests:

```bash
cd frontend
npm test                 # Vitest: components (chips, timeline, drawer, select, answer, context) and helpers
npm run e2e              # Playwright end-to-end against --demo (never approves)
npm run screenshots      # regenerates docs/images/*.png (every page, light and dark, 1440x900)
```

Playwright starts the backend with `uv run python -m app.web --demo --port 8799`. Set
`UV_BIN` if `uv` isn't on `PATH`.

### Design tokens

Colors are CSS variables in `src/index.css` (`:root` for light, `.dark` for dark),
mapped to Tailwind utilities with `@theme inline` (`bg-canvas`, `bg-app`, `bg-surface`,
`bg-bubble`, `text-muted`, `border-border`, `bg-accent`, ...). Light is the default; the
theme can be switched in the sidebar footer or Settings (light, dark, system).

| Token | Light | Use |
|---|---|---|
| `--canvas` | `#e9ebf5` | page background around the app container |
| `--app`, `--surface` | `#ffffff` | app container, cards |
| `--bg` | `#f8f9fc` | page content area |
| `--border` | `#e3e5ee` | 1px card borders |
| `--text` / `--text-muted` / `--text-subtle` | `#171a2b` / `#4a5068` / `#626a82` | text (all AA on white) |
| `--accent` | `#4f46e5` (dark `#7c7cf6`) | primary buttons, active nav, selection, send button |
| `--bubble` | `#ebe9fd` | user message bubbles |
| `--success*` | green | ready / connected / completed / evidence |
| `--warning*` | amber | hypotheses, warnings, anything that writes to GitHub |
| `--danger*` | red | errors, blocked drafts, invalid citations |

- **Type:** Inter (UI) and JetBrains Mono (paths, line ranges, code), self-hosted via Fontsource.
- **Shape:** 16 px app container, 12 px cards, 8 px controls; soft 1px borders and light shadows.
- **Motion:** 150–250 ms transitions, disabled under `prefers-reduced-motion`.
- **Code:** highlight.js core with a few registered languages, themed through the same tokens.
- **Layout:** right panel becomes an overlay below 1200 px; the sidebar collapses to
  icons (with a compact index button) below 1100 px.
