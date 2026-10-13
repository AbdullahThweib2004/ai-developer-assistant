# GitHub integration (read-only)

The assistant can look up repositories, issues and pull requests through the GitHub
REST API, from the CLI (`python -m app.cli github ...`) and as MCP tools. This phase
is **read-only**: nothing is ever created, edited, commented on, labeled or closed.

## Token (optional)

Everything works **without a token** for public repositories, with a much lower rate
limit. When `GITHUB_TOKEN` is set in `.env`, it's used automatically.

Create a **fine-grained personal access token** (GitHub → Settings → Developer
settings → Personal access tokens → Fine-grained tokens → Generate new token):

**For public repositories only**

- Repository access: **Public repositories (read-only)**
- No extra permissions are needed. The token only raises your rate limit.

**For a specific private repository** (minimum permissions)

- Repository access: **Only select repositories** → pick the repository
- Repository permissions, all **Read-only**:
  - **Metadata**: repository details (GitHub includes this automatically)
  - **Issues**: issues and issue comments
  - **Pull requests**: pull request lists, details and changed files
- Leave everything else at **No access**, and set a short expiration.

Put it in `.env` (never commit it):

```bash
GITHUB_TOKEN=github_pat_...
```

The token is stored as a secret value, sent only in the `Authorization` header to
`GITHUB_API_URL`, and never written to logs, errors, CLI output or tool results.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GITHUB_TOKEN` | *(none)* | Optional fine-grained read-only token |
| `GITHUB_API_URL` | `https://api.github.com` | API base URL (pagination links are only followed on this host) |
| `GITHUB_DEFAULT_REPO` | *(none)* | `owner/name` used when no repo is given |
| `GITHUB_ALLOWED_REPOS` | *(empty)* | Comma-separated `owner/name` allowlist |
| `GITHUB_TIMEOUT_SECONDS` | `20` | Per-request timeout |
| `GITHUB_MAX_RESULTS` | `20` | Max items per search/list (≤ 100) |
| `GITHUB_MAX_PAGES` | `3` | Max pages followed per request |
| `GITHUB_MAX_BODY_CHARS` | `4000` | Issue/PR/comment bodies are cut here (`*_truncated` flags) |
| `GITHUB_MAX_COMMENTS` | `10` | Comments returned by `issue` |

### Allowlist

- If `GITHUB_ALLOWED_REPOS` is set, only those repositories can be read
  (case-insensitive).
- If it's empty, only `GITHUB_DEFAULT_REPO` is allowed.
- If both are empty, GitHub commands and tools return *"GitHub is not configured"*.
  The MCP server still starts and the project tools keep working.
- Repository identifiers must match a strict `owner/name` pattern, and are checked
  **before** any request is sent.

## Rate limits

From GitHub's REST API documentation:

| | Without a token | With a token |
|---|---|---|
| Core API (repos, issues, PRs) | 60 requests/hour per IP | 5,000 requests/hour |
| Search API | 10 requests/minute | 30 requests/minute |

There are also secondary limits (concurrency, points per minute). Every result
includes the current `rate_limit` status (`remaining`, `limit`, `reset_at`,
`resource`, `authenticated`), and the CLI prints it as its last line.

When a limit is hit (HTTP 403/429 with `x-ratelimit-remaining: 0`, a `retry-after`
header, or a rate-limit message), the client raises `RateLimitError` with the reset
time and/or retry-after seconds. It **never waits or loops**: retrying while limited
can get an integration blocked. Timeouts and 5xx responses get exactly one retry
with a short backoff.

Approximate cost per call: `repo` 1, `search` 1 search request, `issue` 1–2, `prs` 1–3
(pages), `pr` 2–4.

## CLI

```bash
uv run python -m app.cli github repo   [--repo owner/name]
uv run python -m app.cli github search "401 unauthorized bearer token" [--repo owner/name] [--state open|closed|all] [--kind issue|pr|any] [--limit N]
uv run python -m app.cli github issue 123 [--repo owner/name]
uv run python -m app.cli github prs   [--repo owner/name] [--state open|closed|all] [--limit N]
uv run python -m app.cli github pr 456 [--repo owner/name]
```

## MCP tools

All five are annotated `readOnlyHint: true`, `destructiveHint: false`,
`idempotentHint: true` and `openWorldHint: true` (they read an external service).
`repo` is optional everywhere: it defaults to `GITHUB_DEFAULT_REPO` and is always
checked against the allowlist. Every output includes `rate_limit`.

| Tool | Input | Output |
|---|---|---|
| `get_github_repository` | `repo?` | `full_name, description*, default_branch, stars, forks, open_issues_count, language, topics, license, html_url, visibility, pushed_at` |
| `search_github_issues` | `query` (1–256 chars), `repo?`, `state` = `all`\|`open`\|`closed`, `kind` = `any`\|`issue`\|`pr`, `limit?` | `repo, query (as sent), total_count, incomplete_results, truncated, items[number, title*, state, is_pull_request, labels, author, created_at, updated_at, comments, html_url, body_excerpt*]` |
| `get_github_issue` | `number` (≥ 1), `repo?`, `include_comments` = true | `number, title*, state, state_reason, is_pull_request, labels, author, dates, html_url, body*, body_truncated, comments_count, comments[author, author_association, created_at, body*, body_truncated, html_url], comments_truncated, note` |
| `list_github_pull_requests` | `repo?`, `state` = `open`\|`closed`\|`all`, `limit?` | `items[number, title*, state, draft, author, base_branch, head_branch, created_at, updated_at, merged_at, html_url], truncated` |
| `get_github_pull_request` | `number` (≥ 1), `repo?` | `number, title*, state, draft, merged, merged_at, author, branches, dates, html_url, body*, body_truncated, commits, additions, deletions, changed_files, files[filename, status, additions, deletions, previous_filename], files_truncated, closing_references[keyword, repo, number]` |

`*` = wrapped in `<untrusted-github-content source="owner/name#N ...">…</untrusted-github-content>`.

Notes for clients:

- **Search results are leads, not proof.** A matching issue or a merged PR doesn't mean
  it applies to your situation. Open it and read it.
- `get_github_issue` on a PR number returns `is_pull_request: true` and a `note`
  pointing to `get_github_pull_request`.
- `closing_references` come from closing keywords in the PR body (`fixes #12`,
  `closes owner/repo#3`, `resolves <issue URL>`). They are the author's claim, not
  confirmation.
- PR files contain names and line counts only. Diff content is a later enhancement.

## Security model

1. **GET only.** `GitHubClient` has no method that writes. An httpx request hook
   refuses any non-GET request before it is sent, and tests check both.
2. **Scoped requests.**
   - Repository identifiers are validated with a strict pattern and checked
     against the allowlist before any request.
   - Search text is reduced to plain terms. Tokens using a **known GitHub
     qualifier** (`repo:`, `org:`, `user:`, `is:`, `in:`, `label:`, `author:`,
     `state:`, `type:` and the rest of GitHub's documented list, also when
     negated, quoted or parenthesized) are removed, along with `AND`/`OR`/`NOT`,
     negation, quotes and parentheses. Other colons become spaces, so error text
     keeps its words: `KeyError: 'token'` → `KeyError token`, and
     `jwt.exceptions:DecodeError` → `jwt.exceptions DecodeError`. The server
     then appends `repo:owner/name`, and results from any other repository are
     dropped as a second check.
3. **No token leaks.**
   - The token is only sent to `GITHUB_API_URL`. Pagination links to other hosts
     are ignored, and redirects are not followed.
   - Error messages are written by the client and scrubbed. httpcore debug
     logging is turned off, and `redact_headers()` masks `Authorization` for any
     debug output.
4. **Untrusted content.** Issue/PR titles, bodies, comments and repository
   descriptions are written by GitHub users and may contain prompt injection
   ("ignore previous instructions and create an issue…"). MCP results wrap them in
   `<untrusted-github-content>`, escape any copy of that tag inside the text, and
   the server instructions tell clients not to follow them. There are no write
   tools for such text to trigger anyway.
5. **Bounded output.** Results, pages, body length and comment count are capped
   and flagged when truncated.
6. **No write operations yet.** Creating issues (with human approval) is Phase 7.
