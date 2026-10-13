# Pending LLM checks

These checks need a real LLM provider and have not been run yet. Run them once
a provider is configured, then record the results in the README
("Evaluate answers" → Baseline) together with the model name and `PROMPT_VERSION`.

## 1. Configure a provider

Copy the template and set **one** provider block in `.env` (see the README section
"LLM provider setup"):

```bash
cp .env.example .env   # only if .env doesn't exist yet
```

Variables to set:

| Provider | Required variables |
|---|---|
| Anthropic | `LLM_PROVIDER=anthropic`, `LLM_MODEL`, `ANTHROPIC_API_KEY` (recommended: `LLM_MAX_OUTPUT_TOKENS=4096`) |
| OpenAI | `LLM_PROVIDER=openai_compatible`, `LLM_MODEL`, `OPENAI_API_KEY` (often `LLM_TEMPERATURE=none`) |
| Ollama (local) | `LLM_PROVIDER=openai_compatible`, `LLM_MODEL`, `LLM_BASE_URL=http://localhost:11434/v1` |

Quick check that the provider works (one tiny request):

```bash
uv run pytest -m slow -k real_provider -rs
```

## 2. Build the indexes

```bash
uv run python -m app.cli index --path tests/fixtures/sample_project --rebuild
uv run python -m app.cli index --path . --rebuild
```

## 3. Answer evaluation (fixture project)

```bash
uv run python evaluation/evaluate_answers.py
```

Writes `evaluation/results/answers_<timestamp>.json` and a `_review.md` sheet for
manual scoring (both gitignored). Check in particular that `u01`–`u04`
(unanswerable) have `evidence_flag_correct = yes`. If not, the insufficient-evidence
detection (`is_insufficient` in `app/rag/answer_generator.py`) needs a fix.
Report the failure before changing it.

## 4. `ask` on this repository

```bash
uv run python -m app.cli ask --path . "How does the chunker avoid infinite loops?"
uv run python -m app.cli ask --path . "How is a mismatched embedding model detected?"
uv run python -m app.cli ask --path . "Which CI service runs the tests?"
```

Expected:

- Q1 cites `app/ingestion/chunker.py`.
- Q2 is a known hard case for retrieval: vector search tends to return `embeddings.py`
  rather than `vector_store.py`. Note whether the answer still finds the model check
  or correctly reports insufficient evidence.
- Q3 should be **insufficient evidence**. The project has no CI configuration.

Each command prints token usage and latency. Note them for a cost estimate.

## 5. Record results

Update the README with:

- Answer metrics (`cites_expected_file`, `no_invalid_citations`, `mentions_required`,
  `evidence_flag_correct`, `questions_passed`)
- Model name and provider
- `PROMPT_VERSION` (printed by `ask` and stored in the results JSON)
