# LLM checks

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
uv run python -m app.cli ask --path . "Which CI service runs the tests?"
```

Things to check each time:

- Every unanswerable question in `answer_questions.json` has
  `evidence_flag_correct = yes`.
- The second `ask` question is a known retrieval hard case. An honest
  "insufficient evidence" answer is acceptable; an invented mechanism is not.
- Token usage and latency are printed by `ask` and stored in the results JSON.

Provider setup is described in the README ("LLM provider setup").
