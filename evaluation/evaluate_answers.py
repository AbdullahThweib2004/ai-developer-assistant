"""Baseline answer evaluation with automatic checks plus a manual review sheet.

Requires a real LLM provider (LLM_PROVIDER, LLM_MODEL and the matching API key
in .env). Indexes the fixture project, asks every question in
answer_questions.json, and checks each answer:

- cites_expected_file   answerable: at least one cited source is an expected file
- no_invalid_citations  no [Sx] that wasn't among the provided sources
- mentions_required     answerable: every must_mention term appears (case-insensitive)
- evidence_flag_correct unanswerable -> insufficient_evidence is True;
                        answerable   -> insufficient_evidence is False

Outputs (gitignored): evaluation/results/answers_<timestamp>.json and
evaluation/results/answers_<timestamp>_review.md for manual scoring.

Usage:
    uv run python evaluation/evaluate_answers.py [--path tests/fixtures/sample_project]
        [--questions evaluation/answer_questions.json] [--skip-index]
"""

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from app.config import Settings, get_settings
from app.llm.base import LLMConfigError, LLMError
from app.llm.factory import get_llm_provider
from app.rag.answer_generator import Answer, AnswerGenerator
from app.rag.prompts import PROMPT_VERSION
from app.retrieval.indexer import build_index

EVAL_DIR = Path(__file__).parent
REPO_ROOT = EVAL_DIR.parent
CHECKS = ("cites_expected_file", "no_invalid_citations", "mentions_required", "evidence_flag_correct")


def check_answer(question: dict[str, Any], answer: Answer) -> dict[str, bool | None]:
    """Automatic checks; ``None`` means the check doesn't apply to this question."""
    unanswerable = bool(question.get("expect_insufficient"))
    cited_files = {s.file_path for s in answer.cited_sources}
    text = answer.answer_text.lower()
    return {
        "cites_expected_file": None if unanswerable else bool(cited_files & set(question["expected_files"])),
        "no_invalid_citations": not answer.invalid_citations,
        "mentions_required": None if unanswerable else all(
            term.lower() in text for term in question.get("must_mention", [])
        ),
        "evidence_flag_correct": answer.insufficient_evidence == unanswerable,
    }


def require_real_provider(settings: Settings) -> None:
    if settings.llm_provider == "fake":
        sys.exit("evaluate_answers.py needs a real LLM. Set LLM_PROVIDER to anthropic or openai_compatible in .env.")
    try:
        get_llm_provider(settings)
    except LLMConfigError as exc:
        sys.exit(f"Cannot run the answer evaluation: {exc}")


def evaluate(project: Path, questions: list[dict[str, Any]], skip_index: bool) -> dict[str, Any]:
    settings = get_settings(project_root=project)
    require_real_provider(settings)
    if not skip_index:
        report = build_index(project, rebuild=True, settings=settings)
        print(f"Indexed {report.chunks_indexed} chunks from {report.files_indexed} files ({report.model_name})\n")

    generator = AnswerGenerator(settings)
    rows = []
    for q in questions:
        try:
            answer = generator.answer(q["question"], project)
        except LLMError as exc:
            rows.append({**q, "error": str(exc), "checks": {c: False for c in CHECKS}, "passed": False})
            continue
        checks = check_answer(q, answer)
        rows.append({
            **q,
            "answer_text": answer.answer_text,
            "insufficient_evidence": answer.insufficient_evidence,
            "cited_files": sorted({s.file_path for s in answer.cited_sources}),
            "invalid_citations": answer.invalid_citations,
            "input_tokens": answer.input_tokens,
            "output_tokens": answer.output_tokens,
            "latency_ms": answer.latency.total_ms,
            "checks": checks,
            "passed": all(v for v in checks.values() if v is not None),
        })

    averages: dict[str, float] = {}
    for check in CHECKS:
        values = [r["checks"][check] for r in rows if r["checks"][check] is not None]
        averages[check] = round(mean(values), 3) if values else 0.0
    averages["questions_passed"] = round(mean(r["passed"] for r in rows), 3)
    return {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "project_name": project.resolve().name,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "prompt_version": PROMPT_VERSION,
        "num_questions": len(rows),
        "averages": averages,
        "per_question": rows,
    }


def print_results(results: dict[str, Any]) -> None:
    def mark(value: bool | None) -> str:
        return "  -  " if value is None else (" yes " if value else " NO  ")

    print(f"{'id':<5} {'cites':^5} {'valid':^5} {'terms':^5} {'flag':^5} {'pass':^5}  question")
    for r in results["per_question"]:
        c = r["checks"]
        print(f"{r['id']:<5}{mark(c['cites_expected_file'])}{mark(c['no_invalid_citations'])}"
              f"{mark(c['mentions_required'])}{mark(c['evidence_flag_correct'])}{mark(r['passed'])}  {r['question']}")
        if "error" in r:
            print(f"      error: {r['error']}")
    avg = results["averages"]
    print(f"\nAverages over {results['num_questions']} questions "
          f"({results['llm_provider']} / {results['llm_model']}, prompt {results['prompt_version']}):")
    for key, value in avg.items():
        print(f"  {key:<24} {value:.3f}")


def review_sheet(results: dict[str, Any]) -> str:
    lines = [
        f"# Answer review: {results['timestamp']}",
        "",
        f"Model: `{results['llm_model']}` ({results['llm_provider']}) · prompt `{results['prompt_version']}`",
        "",
        "Score each answer: **correctness** 1-5 (is it right?), **groundedness** 1-5",
        "(is every project claim supported by a cited source?).",
        "",
    ]
    for r in results["per_question"]:
        expected = "unanswerable (should flag insufficient evidence)" if r.get("expect_insufficient") \
            else f"expected files: {', '.join(r['expected_files'])}"
        lines += [
            f"## {r['id']}: {r['question']}",
            "",
            f"*{expected}*; cited: {', '.join(r.get('cited_files', [])) or 'none'}",
            "",
            "> " + (r.get("answer_text") or f"ERROR: {r.get('error')}").replace("\n", "\n> "),
            "",
            "- Correctness (1-5): ",
            "- Groundedness (1-5): ",
            "- Notes: ",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "tests/fixtures/sample_project")
    parser.add_argument("--questions", type=Path, default=EVAL_DIR / "answer_questions.json")
    parser.add_argument("--skip-index", action="store_true", help="Use the existing index")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    results = evaluate(args.path, questions, args.skip_index)
    print_results(results)

    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_file = out_dir / f"answers_{stamp}.json"
    review_file = out_dir / f"answers_{stamp}_review.md"
    json_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    review_file.write_text(review_sheet(results), encoding="utf-8")
    print(f"\nSaved {json_file.relative_to(REPO_ROOT)} and {review_file.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
