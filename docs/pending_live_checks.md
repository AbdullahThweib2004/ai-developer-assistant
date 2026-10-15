# Live checks

The Phase 3 checks that need a real LLM provider have been run (model
`anthropic/claude-haiku-5.5` via OpenRouter, prompt `rag-v1`). The results are in the
README under "Evaluate answers". Re-run them after changing the prompt, the model or
retrieval:

```bash
uv run python -m app.cli index --path tests/fixtures/sample_project --rebuild
uv run python -m app.cli index --path . --rebuild
uv run python evaluation/evaluate_answers.py
uv run python -m app.cli ask --path . "How does the chunker avoid infinite loops?"
uv run python -m app.cli ask --path . "How is a mismatched embedding model detected?"
uv run python -m app.cli ask --path . "Which cloud provider is this app deployed to in production?"
```

Things to check each time:

- Every unanswerable question in `answer_questions.json` has
  `evidence_flag_correct = yes`.
- The second `ask` question is a known retrieval hard case. An honest
  "insufficient evidence" answer is acceptable; an invented mechanism is not.
- Token usage and latency are printed by `ask` and stored in the results JSON.

Provider setup is described in the README ("LLM provider setup").

## Phase 7: issue creation (sandbox only)

Phase 7 was verified live: one cancelled draft, then one approved issue on the sandbox
repo, checked with `gh issue view` and the audit log. To repeat it, use a sandbox
repository listed in `GITHUB_WRITE_ALLOWED_REPOS`:

```bash
# The sandbox must be reachable (this uses the read client; the write token is used only for creation)
uv run python -m app.cli github repo --repo OWNER/SANDBOX-REPO
uv run python -m app.cli index --path examples/fastapi-auth-demo --rebuild
uv run python -m app.cli investigate --path examples/fastapi-auth-demo --mode project --propose-issue \
  "Why do requests to /users/me return 401 even though I send a valid bearer token? Please propose a GitHub issue."
# Type anything except the repo name to cancel; type the exact repo name to create.
gh issue view <number> --repo OWNER/SANDBOX-REPO
cat audit/github_writes.jsonl
```
