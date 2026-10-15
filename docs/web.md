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
| POST | `/ask` | `{path, question, top_k?, file_types?}`: one-shot RAG answer (SSE) |
| POST | `/sessions` | `{path, repo?, mode}`: start an investigation session |
| GET / DELETE | `/sessions/{id}` | read or end a session (stops its MCP server process) |
| POST | `/investigate` | `{session_id, question}`: run the tool loop (SSE) |
| POST | `/sessions/{id}/drafts` | `{turn_id, repo?}`: draft an issue from an answer |
| GET / PUT | `/drafts/{id}` | read, or edit `{title, body, labels}` (new hash, voids approvals) |
| GET | `/drafts/{id}/similar` | similar open issues in the destination repo |
| POST | `/drafts/{id}/approve` | `{draft_hash, typed_repo_confirmation, confirm_similar}` |
| POST | `/drafts/{id}/cancel` | cancel (audited) |

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

```
frontend/
├── index.html              # applies the saved/system theme before first paint
├── vite.config.ts          # React + Tailwind plugins, /api proxy for dev, Vitest config
├── src/
│   ├── App.tsx             # state: projects, sessions, messages, panels, drawer
│   ├── index.css           # design tokens (CSS variables), Tailwind theme, code theme
│   ├── lib/                # api.ts (fetch + SSE), types, format, citations, highlight, theme
│   └── components/         # Sidebar, Composer, MessageView, StepTimeline, AnswerView,
│                           # CitationChip, EvidencePanel, CodeBlock, DraftDrawer, Toasts, ui
└── e2e/                    # Playwright: app.spec.ts, screenshots.spec.ts (demo backend)
```

Development with hot reload:

```bash
uv run ai-dev-assistant web --dev --demo   # writes frontend/.env.development.local (gitignored)
cd frontend && npm run dev                 # http://localhost:5173, proxies /api to the backend
```

`--dev` allows the Vite origin and writes the session token for Vite to read.

Tests:

```bash
cd frontend
npm test                 # Vitest: citation chips, step timeline, approval drawer
npm run e2e              # Playwright end-to-end against --demo (never approves)
npm run screenshots      # regenerates docs/images/*.png (dark and light, 1440x900)
```

Playwright starts the backend with `uv run python -m app.web --demo --port 8799`. Set
`UV_BIN` if `uv` isn't on `PATH`.

### Design tokens

Colors are CSS variables in `src/index.css` (`:root` for light, `.dark` for dark),
mapped to Tailwind utilities with `@theme inline` (`bg-surface`, `text-muted`,
`border-border`, `bg-accent`, ...). The theme follows the system setting until you
pick one in the sidebar.

- **Neutrals:** zinc. **Accent:** indigo (`#4f46e5` light, `#7c7cf0` dark), used only
  for interactive and "current" state.
- **Semantic colors** only carry meaning: green for evidence and success, amber for
  hypotheses and anything that writes to GitHub, red for errors, blocked drafts and
  invalid citations.
- **Type:** Inter (UI) and JetBrains Mono (code, paths, numbers), self-hosted through
  Fontsource.
- 4/8 px spacing, 8–12 px radii, 150–250 ms transitions, disabled under
  `prefers-reduced-motion`.
- Code highlighting uses highlight.js core with a few registered languages: it is
  synchronous (no layout shift), small, and themed through the same tokens.
