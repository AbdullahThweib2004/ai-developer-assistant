"""Command-line entry point.

Usage:
    python -m app.cli ingest --path ./repo [--output chunks.jsonl] [-v]
    python -m app.cli index  --path ./repo [--rebuild]
    python -m app.cli search --path ./repo "how is auth done?" [--top-k 5] [--type py]
    python -m app.cli stats  --path ./repo
"""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from app.config import Settings, get_settings
from app.ingestion.models import Chunk
from app.ingestion.pipeline import PipelineResult, run_pipeline
from app.retrieval.indexer import IndexReport, build_index, open_store
from app.retrieval.retriever import RetrievedChunk, Retriever
from app.retrieval.vector_store import VectorStoreError

PREVIEW_CHARS = 300


# ---- formatting ---------------------------------------------------------


def format_summary(result: PipelineResult) -> str:
    lines = [
        f"Files found (eligible):  {result.files_found}",
        f"Skipped:                 {len(result.skipped)}",
    ]
    for reason, count in sorted(Counter(s.reason.value for s in result.skipped).items()):
        lines.append(f"  - {reason:<22} {count}")
    lines.append(f"Documents parsed:        {len(result.documents)}")
    lines.append(f"Total chunks:            {len(result.chunks)}")
    for file_type, count in sorted(Counter(c.file_type for c in result.chunks).items()):
        lines.append(f"  - {file_type:<22} {count}")
    return "\n".join(lines)


def format_index_report(report: IndexReport) -> str:
    lines = [
        f"Collection:       {report.collection_name}",
        f"Embedding model:  {report.model_name}",
        f"Files indexed:    {report.files_indexed}",
        f"Files skipped:    {report.files_skipped}",
        f"Chunks indexed:   {report.chunks_indexed}",
    ]
    for file_type, count in report.chunks_per_file_type.items():
        lines.append(f"  - {file_type:<14} {count}")
    lines.append(f"Duration:         {report.duration_seconds:.2f}s")
    return "\n".join(lines)


def format_results(results: list[RetrievedChunk]) -> str:
    if not results:
        return "No results."
    blocks = []
    for rank, r in enumerate(results, start=1):
        preview = " ".join(r.content.split())
        if len(preview) > PREVIEW_CHARS:
            preview = preview[:PREVIEW_CHARS].rstrip() + "..."
        blocks.append(f"{rank}. [{r.score:.3f}] {r.file_path} (chunk {r.chunk_index})\n   {preview}")
    return "\n\n".join(blocks)


def write_jsonl(chunks: list[Chunk], output: Path) -> None:
    with output.open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(chunk.model_dump_json() + "\n")


# ---- commands -----------------------------------------------------------


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> None:
    result = run_pipeline(settings)
    print(format_summary(result))
    if args.output:
        write_jsonl(result.chunks, args.output)
        print(f"Wrote {len(result.chunks)} chunks to {args.output}")


def cmd_index(args: argparse.Namespace, settings: Settings) -> None:
    report = build_index(settings.project_root, rebuild=args.rebuild, settings=settings)
    print(format_index_report(report))


def cmd_search(args: argparse.Namespace, settings: Settings) -> None:
    retriever = Retriever.for_project(settings.project_root, settings)
    results = retriever.search(args.query, top_k=args.top_k, file_types=args.type)
    print(format_results(results))


def cmd_stats(args: argparse.Namespace, settings: Settings) -> None:
    # Inspect only: report whichever model the index was built with.
    store = open_store(settings, settings.project_root, model_name=None)
    files = store.list_indexed_files()
    print(f"Collection:       {store.name}")
    print(f"Chunks:           {store.count()}")
    print(f"Embedding model:  {store.stored_model()}")
    print(f"Files indexed:    {len(files)}")
    for path in files:
        print(f"  - {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_command(name: str, help_text: str) -> argparse.ArgumentParser:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--path", type=Path, help="Repository path (overrides PROJECT_ROOT)")
        cmd.add_argument("-v", "--verbose", action="store_true", help="Show INFO logs")
        return cmd

    ingest = add_command("ingest", "Load, parse and chunk a repository")
    ingest.add_argument("--output", type=Path, help="Write chunks to this JSONL file")
    ingest.set_defaults(handler=cmd_ingest)

    index = add_command("index", "Embed a repository's chunks into Chroma")
    index.add_argument("--rebuild", action="store_true", help="Replace an existing index")
    index.set_defaults(handler=cmd_index)

    search = add_command("search", "Semantic search over an indexed repository")
    search.add_argument("query", help="Natural-language question or keywords")
    search.add_argument("--top-k", type=int, default=None, help="Number of results (default: RETRIEVAL_TOP_K)")
    search.add_argument("--type", action="append", help="Only this file type, e.g. py (repeatable)")
    search.set_defaults(handler=cmd_search)

    stats = add_command("stats", "Show what is indexed for a repository")
    stats.set_defaults(handler=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    overrides = {"project_root": args.path} if args.path else {}
    try:
        settings = get_settings(**overrides)
        args.handler(args, settings)
    except (ValueError, NotADirectoryError, VectorStoreError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
