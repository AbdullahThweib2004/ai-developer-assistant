# Security

The assistant reads untrusted code and GitHub text, holds an LLM key and a GitHub token,
and can create a GitHub issue. This page lists the trust boundaries, the main threats,
how each is mitigated and which tests check it.

## Trust boundaries

| Boundary | Trusted side | Untrusted side |
|---|---|---|
| Project files → tools | allowlisted roots, loader rules | file contents (code, docs, config) |
| GitHub → tools | read allowlist, GET-only client | issue/PR titles, bodies, comments |
| Tool results → LLM | system prompt, session config | everything a tool returns |
| LLM → tools | tool schemas, session limits | the model's tool choices and arguments |
| LLM → GitHub writes | human approval in CLI/web/MCP elicitation | any draft the model proposes |
| Browser → local web API | per-launch token, same origin | other sites, other hosts on the network |

## Threats and mitigations

**Prompt injection** (instructions hidden in files or issues).
- All tool output is wrapped in `<untrusted-...>` tags; the system prompt says it is data and
  can never authorize actions.
- The model cannot choose the project or repository: the session injects them, and tools
  have no `repo`/`path` argument the model controls. Unknown tools and extra arguments are
  rejected; tool calls are bounded (`ORCH_*` limits).
- All model-facing tools are read-only. Drafting an issue is only offered when the user asks.
- Tests: `test_model_cannot_choose_project_or_repo`, `test_github_calls_get_the_session_repo`,
  `test_unknown_tool_and_bad_arguments_are_rejected`, `test_wrap_untrusted_escapes_source_attribute`;
  live: [adversarial evaluation](evaluation.md) (6 cases, 0 forbidden calls, 0 leaks).

**Secret exposure** (keys, `.env`, tokens in drafts or logs).
- `.env`, key files and secret-looking names are never read, indexed or listed (only counts
  of skipped files are reported). `.env.example` is allowed.
- The MCP server process gets no LLM settings or keys. The web API never returns keys
  (`/api/status` reports provider and model only).
- Issue drafts are secret-scanned (API keys, GitHub tokens, private keys, JWTs, high-entropy
  strings); a hit blocks approval and the error never echoes the value. Absolute local paths
  are replaced. The audit log stores metadata only (no title, body or token).
- Read and write GitHub tokens are separate and never mixed.
- Tests: `test_env_is_sensitive_but_env_example_is_allowed`, `test_secret_scan_blocks_and_never_echoes`,
  `test_status_never_contains_secrets`, `test_audit_records_have_no_body_title_or_token`,
  `test_tokens_are_never_mixed`, `test_audit_endpoint_returns_metadata_only`.

**Path traversal** (reading outside the project).
- Every project path is resolved against `MCP_ALLOWED_PROJECT_ROOTS`; every file through the
  loader's rules (inside the root, no symlinks out, no ignored or binary files).
- Tests: `test_resolve_within_root_rejects_traversal`, `test_symlinks_pointing_outside_root_are_skipped`,
  `test_symlink_disguising_secret_is_skipped`, `test_paths_outside_allowed_roots_are_rejected_without_leaking`,
  `test_project_file_viewer_reads_through_the_security_rules`.

**Unintended writes** (an issue created without the user's intent).
- Writing is off by default and needs `GITHUB_WRITE_ENABLED`, a separate token and a write
  allowlist. The write client only sends `POST /repos/{owner}/{repo}/issues`, never retries.
- A human must type the exact repository name; the approval is single-use, expires, and is
  bound to the draft's hash (any edit voids it). One attempt per draft. No tool, prompt or API
  route lets the model approve.
- Tests: `test_approve_requires_exact_typed_repo_and_creates_once`, `test_edit_changes_hash_and_old_hash_is_refused`,
  `test_write_client_refuses_everything_but_create_issue`, `test_api_has_no_route_the_llm_could_use_to_approve`,
  `test_read_allowlist_does_not_grant_write`; e2e: approval drawer stays disabled until the exact name is typed.

**Local web API** (other websites or hosts calling it).
- Binds to `127.0.0.1`; other interfaces need `--allow-network` and print a warning.
- `Host` must be the local address (blocks DNS rebinding); foreign `Origin` and
  `Sec-Fetch-Site: cross-site` are refused; no CORS headers. Every `/api` call needs the
  per-launch token injected into the page. Errors are generic; no stack traces.
- Tests: `test_token_required_on_every_api_route`, `test_host_header_checked_against_dns_rebinding`,
  `test_cross_origin_requests_refused`, `test_refuses_network_bind_without_flag`.

**Conversation history** (data at rest).
- The web app stores conversations locally in `DATA_DIR/conversations.db` (SQLite, default
  `./data`, gitignored; directory mode 700, file mode 600). **History can contain code
  excerpts** from your projects, the questions you asked and the answers.
- Before anything is written, text goes through the same secret patterns as issue drafts and
  matches are replaced with `[redacted]`; the app's own keys and tokens are never stored.
- Approvals and drafts are never stored: after a restart, an issue must be drafted and
  approved again. Each conversation is bound to one project, and the server refuses a
  question whose project doesn't match its conversation.
- Settings → "Save conversation history" (on by default) keeps new chats in memory only;
  "Clear history" deletes all saved conversations.
- Tests: `test_secrets_are_never_stored`, `test_approvals_and_drafts_are_never_stored`,
  `test_project_mismatch_is_rejected_server_side`, `test_database_file_is_private`.

## Dependency audit (2026-10-15)

- `npm audit --omit=dev`: 0 vulnerabilities.
- `pip-audit`: 5 advisories, all in `chromadb` 1.5.9 (CVE-2026-45829, -45830, -45831, -45833).
  They affect the Chroma **HTTP server** (remote code via `trust_remote_code`, tenant
  authorization). This project only uses the embedded `PersistentClient` and never starts a
  Chroma server, so they are not reachable here. No fixed release exists yet; upgrade when one does.
- `torch` (CPU wheel from the PyTorch index) is not on PyPI and was not audited.

## Known limitations

- Prompt injection is reduced, not solved: a model can still repeat misleading text from a
  file in its answer (as data), even though it cannot act on it. Answers cite their sources
  so this can be checked.
- The secret scanner uses patterns and entropy; unusual secret formats can slip through.
  The human review of the full draft is the last line of defence.
- The web token lives in the page: any process that can read pages served on
  `127.0.0.1` as the same user can use the API. Don't use `--allow-network` on untrusted networks.
- Local paths can appear in answers if a file mentions them; only issue drafts are scrubbed.
- The adversarial evaluation is small (6 cases, one model, one run).
- The history database is not encrypted; anyone who can read your user's files can read it.
  Redaction is pattern-based, like the draft scanner.
