"""Baseline retrieval evaluation at the file level.

For each question, the retriever's chunks are collapsed into a ranked list of
distinct files (first occurrence wins), cut to the top ``k``, and compared with
the question's ``relevant_files``:

- Precision@k = relevant files in top k / k
- Recall@k    = relevant files in top k / number of relevant files
- MRR         = mean of 1 / rank of the first relevant file (0 if none in top k)

Usage:
    uv run python evaluation/evaluate_retrieval.py [--path tests/fixtures/sample_project]
        [--questions evaluation/questions.json] [--k 5] [--skip-index]
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from app.config import get_settings
from app.retrieval.indexer import build_index
from app.retrieval.retriever import Retriever

EVAL_DIR = Path(__file__).parent
REPO_ROOT = EVAL_DIR.parent
# Chunks fetched per question before collapsing to files.
CHUNKS_PER_FILE_SLOT = 4


def rank_files(chunk_files: list[str], k: int) -> list[str]:
    """Distinct files in order of their best-ranked chunk, top ``k``."""
    ranked: list[str] = []
    for path in chunk_files:
        if path not in ranked:
            ranked.append(path)
    return ranked[:k]


def score_question(retrieved: list[str], relevant: list[str], k: int) -> dict[str, float]:
    hits = [f for f in retrieved if f in relevant]
    first_hit = next((rank for rank, f in enumerate(retrieved, start=1) if f in relevant), None)
    return {
        "precision_at_k": len(hits) / k,
        "recall_at_k": len(hits) / len(relevant),
        "reciprocal_rank": 1 / first_hit if first_hit else 0.0,
    }


def evaluate(project: Path, questions: list[dict[str, Any]], k: int, skip_index: bool) -> dict[str, Any]:
    settings = get_settings(project_root=project)
    if not skip_index:
        report = build_index(project, rebuild=True, settings=settings)
        print(f"Indexed {report.chunks_indexed} chunks from {report.files_indexed} files "
              f"with {report.model_name} in {report.duration_seconds:.1f}s\n")

    retriever = Retriever.for_project(project, settings)
    per_question = []
    for q in questions:
        chunks = retriever.search(q["question"], top_k=k * CHUNKS_PER_FILE_SLOT, min_score=0.0)
        retrieved = rank_files([c.file_path for c in chunks], k)
        scores = score_question(retrieved, q["relevant_files"], k)
        per_question.append({**q, "retrieved_files": retrieved, **scores})

    averages = {
        metric: round(mean(r[metric] for r in per_question), 4)
        for metric in ("precision_at_k", "recall_at_k", "reciprocal_rank")
    }
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_name": project.resolve().name,
        "embedding_model": retriever.provider.name,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "k": k,
        "num_questions": len(per_question),
        "averages": {"precision_at_k": averages["precision_at_k"],
                     "recall_at_k": averages["recall_at_k"],
                     "mrr": averages["reciprocal_rank"]},
        "per_question": per_question,
    }


def print_results(results: dict[str, Any]) -> None:
    k = results["k"]
    print(f"{'id':<5} {'P@' + str(k):>5} {'R@' + str(k):>5} {'RR':>5}  question")
    for r in results["per_question"]:
        print(f"{r['id']:<5} {r['precision_at_k']:>5.2f} {r['recall_at_k']:>5.2f} "
              f"{r['reciprocal_rank']:>5.2f}  {r['question']}")
        print(f"{'':<25}relevant={r['relevant_files']} retrieved={r['retrieved_files']}")
    avg = results["averages"]
    print(f"\nAverages over {results['num_questions']} questions ({results['embedding_model']}):")
    print(f"  Precision@{k}: {avg['precision_at_k']:.3f}")
    print(f"  Recall@{k}:    {avg['recall_at_k']:.3f}")
    print(f"  MRR:          {avg['mrr']:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "tests/fixtures/sample_project")
    parser.add_argument("--questions", type=Path, default=EVAL_DIR / "questions.json")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--skip-index", action="store_true", help="Use the existing index")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    results = evaluate(args.path, questions, args.k, args.skip_index)
    print_results(results)

    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_file = out_dir / f"retrieval_{stamp}.json"
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved {out_file.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
