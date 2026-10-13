"""Command-line entry point.

Usage:
    python -m app.cli ingest --path ./some/repo [--output chunks.jsonl] [-v]
"""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.ingestion.models import Chunk
from app.ingestion.pipeline import PipelineResult, run_pipeline


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


def write_jsonl(chunks: list[Chunk], output: Path) -> None:
    with output.open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(chunk.model_dump_json() + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="Load, parse and chunk a repository")
    ingest.add_argument("--path", type=Path, help="Repository to index (overrides PROJECT_ROOT)")
    ingest.add_argument("--output", type=Path, help="Write chunks to this JSONL file")
    ingest.add_argument("-v", "--verbose", action="store_true", help="Log every skipped file")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    overrides = {"project_root": args.path} if args.path else {}
    try:
        settings = get_settings(**overrides)
        result = run_pipeline(settings)
    except (ValueError, NotADirectoryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(format_summary(result))
    if args.output:
        write_jsonl(result.chunks, args.output)
        print(f"Wrote {len(result.chunks)} chunks to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
