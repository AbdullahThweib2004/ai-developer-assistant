"""Evaluate the orchestrator's tool selection and evidence on fixed cases.

Each case in orchestration_questions.json names the tool categories it should use
(project, github, both, or none) and, where known, files or issue/PR numbers that
count as the expected evidence. Per case and on average this reports:

- tool_selection_correct  categories used == categories expected
- unnecessary_calls       calls in categories the case didn't need
- limit_hit               any orchestration limit reached
- citations_valid         no invalid [S#]/[G#] citations
- expected_evidence       an expected file was cited, or an expected issue/PR was found
                          (None when the case lists no expectations)
- bugs_found              for cases with "expected_bugs": which known bugs the answer
                          identifies (every regex in a bug's "all_patterns" matches);
                          bugs_ok = at least "min_bugs" found

Cases may set "project" (path) and "repo" to override the defaults, and
"optional_tools" for tool groups that are acceptable but not required.
- tool_calls, llm_calls, tokens, latency

Tools run through the real MCP server (MCPClientBackend). Needs a working
tool-capable LLM; exits with a clear message otherwise.

Usage:
    uv run python evaluation/evaluate_orchestration.py [--repo fastapi/fastapi] [--pause 5] [--skip-index]
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from app.config import get_settings
from app.llm.base import LLMConfigError, LLMError
from app.orchestration.assistant import (
    GITHUB_TOOLS,
    PROJECT_TOOLS,
    InvestigationResult,
    Investigator,
    SessionConfig,
    build_tool_llm,
)
from app.orchestration.prompts import ORCH_PROMPT_VERSION
from app.orchestration.tool_backends import MCPClientBackend
from app.retrieval.indexer import build_index

EVAL_DIR = Path(__file__).parent
REPO_ROOT = EVAL_DIR.parent


def categories_used(result: InvestigationResult) -> set[str]:
    """Tool categories the assistant actually tried (executed, failed or cached; not rejected)."""
    used = set()
    for step in result.trace:
        if step.kind != "tool" or step.status == "rejected":
            continue
        if step.name in PROJECT_TOOLS:
            used.add("project")
        elif step.name in GITHUB_TOOLS:
            used.add("github")
    return used


def bugs_found(case: dict[str, Any], answer: str) -> list[str]:
    return [bug["id"] for bug in case.get("expected_bugs", [])
            if all(re.search(pattern, answer) for pattern in bug["all_patterns"])]


def score_case(case: dict[str, Any], result: InvestigationResult) -> dict[str, Any]:
    expected = set(case["expected_tools"])
    acceptable = expected | set(case.get("optional_tools", []))
    used = categories_used(result)
    unneeded = 0
    for step in result.trace:
        if step.kind == "tool" and step.status != "rejected":
            category = "project" if step.name in PROJECT_TOOLS else "github" if step.name in GITHUB_TOOLS else None
            unneeded += int(category is not None and category not in acceptable)
    found_bugs = bugs_found(case, result.answer_text)

    cited_files = {s.file_path for s in result.project_sources if s.cited}
    found_issues = {s.number for s in result.github_sources}
    checks = []
    if case["expected_files"]:
        checks.append(bool(cited_files & set(case["expected_files"])))
    if case["expected_issues"]:
        checks.append(bool(found_issues & set(case["expected_issues"])))
    return {
        "expected_tools": sorted(expected),
        "used_tools": sorted(used),
        "tool_selection_correct": expected <= used <= acceptable,
        "bugs_found": found_bugs,
        "bugs_ok": len(found_bugs) >= case["min_bugs"] if case.get("expected_bugs") else None,
        "unnecessary_calls": unneeded,
        "limit_hit": bool(result.limits_hit),
        "limits": result.limits_hit,
        "citations_valid": not result.invalid_citations,
        "invalid_citations": result.invalid_citations,
        "expected_evidence": all(checks) if checks else None,
        "cited_files": sorted(f for f in cited_files if f),
        "found_issues": sorted(n for n in found_issues if n),
        "tool_calls": result.tool_calls,
        "llm_calls": result.llm_calls,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "latency_ms": result.latency.total_ms,
        "github_disabled": result.github_disabled,
    }


def averages(rows: list[dict[str, Any]]) -> dict[str, float]:
    def avg(key: str) -> float:
        values = [r[key] for r in rows if r.get(key) is not None]
        return round(mean(float(v) for v in values), 3) if values else 0.0

    return {
        "tool_selection_accuracy": avg("tool_selection_correct"),
        "unnecessary_calls_per_case": avg("unnecessary_calls"),
        "limit_hit_rate": avg("limit_hit"),
        "citation_validity": avg("citations_valid"),
        "expected_evidence_rate": avg("expected_evidence"),
        "bug_detection_rate": avg("bugs_ok"),
        "tool_calls_per_case": avg("tool_calls"),
        "llm_calls_per_case": avg("llm_calls"),
        "input_tokens_total": float(sum(r["input_tokens"] for r in rows)),
        "output_tokens_total": float(sum(r["output_tokens"] for r in rows)),
        "latency_s_avg": round(avg("latency_ms") / 1000, 2),
    }


def review_sheet(results: dict[str, Any]) -> str:
    lines = [f"# Orchestration review: {results['timestamp']}", "",
             f"Model `{results['model']}` · prompt `{results['prompt_version']}` · repo `{results['repo']}`", "",
             "Score each: **correctness** 1-5, **groundedness** 1-5 (claims supported by cited tool results), "
             "**tool choice** 1-5.", ""]
    for r in results["per_case"]:
        lines += [f"## {r['id']}: {r['question']}", "",
                  f"*expected tools {r['expected_tools'] or ['none']}, used {r['used_tools'] or ['none']}; "
                  f"{r['tool_calls']} calls; cited files {r['cited_files'] or '-'}; issues found {r['found_issues'] or '-'}*",
                  "", "> " + r.get("answer_text", f"ERROR: {r.get('error')}").replace("\n", "\n> "), "",
                  "- Correctness (1-5): ", "- Groundedness (1-5): ", "- Tool choice (1-5): ", "- Notes: ", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "tests/fixtures/sample_project")
    parser.add_argument("--repo", default="fastapi/fastapi")
    parser.add_argument("--cases", type=Path, default=EVAL_DIR / "orchestration_questions.json")
    parser.add_argument("--pause", type=float, default=0.0, help="Seconds between cases (GitHub search limits)")
    parser.add_argument("--skip-index", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    settings = get_settings(project_root=args.path)
    if settings.llm_provider == "fake":
        sys.exit("evaluate_orchestration.py needs a real LLM: set LLM_PROVIDER/LLM_MODEL and the API key in .env.")
    try:
        llm = build_tool_llm(settings)
    except (LLMConfigError, LLMError) as exc:
        sys.exit(f"Cannot run the orchestration evaluation: {exc}")

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not args.skip_index:
        report = build_index(args.path, rebuild=True, settings=settings)
        print(f"Indexed {report.chunks_indexed} chunks ({report.model_name})\n")

    rows: list[dict[str, Any]] = []
    backends: dict[Path, MCPClientBackend] = {}
    try:
        for i, case in enumerate(cases):
            if i and args.pause:
                time.sleep(args.pause)
            project = (REPO_ROOT / case["project"]).resolve() if case.get("project") else args.path.resolve()
            repo = case["repo"] if "repo" in case else args.repo
            if project not in backends:
                if not args.skip_index and project != args.path.resolve():
                    build_index(project, rebuild=True, settings=settings)
                backends[project] = MCPClientBackend(settings, project)
            investigator = Investigator(settings, llm, backends[project],
                                        SessionConfig(project_path=project, repo=repo, mode="auto"))
            try:
                result = investigator.ask(case["question"])
            except LLMError as exc:
                rows.append({**case, "error": str(exc), "tool_selection_correct": False, "unnecessary_calls": 0,
                             "limit_hit": False, "citations_valid": False, "expected_evidence": False,
                             "tool_calls": 0, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                             "latency_ms": 0.0, "used_tools": [], "cited_files": [], "found_issues": []})
                print(f"{case['id']}: ERROR {exc}")
                continue
            row = {**case, **score_case(case, result), "answer_text": result.answer_text,
                   "trace": [t.model_dump(exclude_none=True) for t in result.trace]}
            rows.append(row)
            mark = "ok " if row["tool_selection_correct"] else "XX "
            evidence = {True: "yes", False: "NO", None: "-"}[row["expected_evidence"]]
            bugs = f" | bugs {row['bugs_found'] or 'none'}" if case.get("expected_bugs") else ""
            print(f"{case['id']} {mark} expected {row['expected_tools'] or ['none']} used {row['used_tools'] or ['none']}"
                  f" | calls {row['tool_calls']} (unneeded {row['unnecessary_calls']}) | evidence {evidence}{bugs}"
                  f" | cites valid {row['citations_valid']} | limits {row['limits'] or '-'}"
                  f" | tokens {row['input_tokens']}/{row['output_tokens']} | {row['latency_ms'] / 1000:.1f}s")
    finally:
        for backend in backends.values():
            backend.close()

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_name": args.path.resolve().name, "repo": args.repo, "model": llm.model,
        "provider": llm.provider, "prompt_version": ORCH_PROMPT_VERSION,
        "limits": {"max_tool_calls": settings.orch_max_tool_calls, "max_iterations": settings.orch_max_iterations,
                   "max_github_calls": settings.orch_max_github_calls},
        "num_cases": len(rows), "averages": averages(rows), "per_case": rows,
    }
    print(f"\nAverages over {len(rows)} cases ({llm.model}, prompt {ORCH_PROMPT_VERSION}):")
    for key, value in results["averages"].items():
        print(f"  {key:<28} {value}")

    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_file, md_file = out_dir / f"orchestration_{stamp}.json", out_dir / f"orchestration_{stamp}_review.md"
    json_file.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    md_file.write_text(review_sheet(results), encoding="utf-8")
    print(f"\nSaved {json_file.relative_to(REPO_ROOT)} and {md_file.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
