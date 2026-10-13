"""Command-line entry point.

Usage:
    python -m app.cli ingest --path ./repo [--output chunks.jsonl] [-v]
    python -m app.cli index  --path ./repo [--rebuild]
    python -m app.cli search --path ./repo "how is auth done?" [--top-k 5] [--type py]
    python -m app.cli stats  --path ./repo
    python -m app.cli ask    --path ./repo "How does login work?" [--top-k 5] [--type py] [--show-sources]
    python -m app.cli github repo|search|issue|prs|pr ... [--repo owner/name]   (read-only)
"""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from app.config import Settings, get_settings
from app.integrations.github_client import GitHubClient, resolve_repo
from app.integrations.github_errors import GitHubError
from app.integrations.github_models import RateLimitStatus
from app.ingestion.models import Chunk
from app.ingestion.pipeline import PipelineResult, run_pipeline
from app.llm.base import LLMError
from app.rag.answer_generator import Answer, AnswerGenerator
from app.retrieval.indexer import IndexReport, build_index, index_stats
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


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "..."


def format_results(results: list[RetrievedChunk]) -> str:
    if not results:
        return "No results."
    blocks = []
    for rank, r in enumerate(results, start=1):
        blocks.append(f"{rank}. [{r.score:.3f}] {r.file_path} (chunk {r.chunk_index})\n   {preview(r.content)}")
    return "\n\n".join(blocks)


def format_answer(answer: Answer, show_sources: bool = False) -> str:
    lines = [answer.answer_text, ""]
    if answer.insufficient_evidence:
        lines.append("! Insufficient evidence: the project sources don't fully answer this question.")
    for suggestion in answer.suggestions:
        lines.append(f"  - {suggestion}")
    if answer.invalid_citations:
        lines.append(f"! Invalid citations (not among the provided sources): {', '.join(answer.invalid_citations)}")
    lines.append("")
    lines.append("Cited sources:" if answer.cited_sources else "Cited sources: none")
    for src in answer.cited_sources:
        lines.append(f"  [{src.source_id}] {src.file_path} ({src.location}, score {src.score:.3f})")
    if answer.dropped_sources:
        lines.append(f"Dropped for context budget: {len(answer.dropped_sources)} chunk(s)")
    if show_sources:
        lines.append("")
        lines.append("Retrieved chunks:")
        lines.append(format_results(answer.all_retrieved_sources))
    lines.append("")
    tokens = f"{answer.input_tokens} in / {answer.output_tokens} out" if answer.model else "no LLM call"
    lines.append(f"Model: {answer.model or '-'} ({answer.provider or '-'}) | tokens: {tokens} | prompt: {answer.prompt_version}")
    lat = answer.latency
    lines.append(f"Latency: retrieval {lat.retrieval_ms:.0f} ms, LLM {lat.llm_ms:.0f} ms, total {lat.total_ms:.0f} ms")
    return "\n".join(lines)


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
    stats = index_stats(settings, settings.project_root)
    print(f"Collection:       {stats.collection_name}")
    print(f"Chunks:           {stats.chunk_count}")
    print(f"Embedding model:  {stats.model_name}")
    print(f"Files indexed:    {len(stats.files)}")
    for path in stats.files:
        print(f"  - {path}")


def cmd_ask(args: argparse.Namespace, settings: Settings) -> None:
    answer = AnswerGenerator(settings).answer(
        args.question, settings.project_root, top_k=args.top_k, file_types=args.type
    )
    print(format_answer(answer, show_sources=args.show_sources))


def format_rate_limit(rate: RateLimitStatus | None, authenticated: bool) -> str:
    auth = "token" if authenticated else "no token"
    if rate is None or rate.remaining is None:
        return f"GitHub rate limit: unknown ({auth})"
    reset = rate.reset_at.strftime("%H:%M:%S UTC") if rate.reset_at else "?"
    return f"GitHub rate limit ({rate.resource or 'core'}): {rate.remaining}/{rate.limit} remaining, resets {reset} ({auth})"


def _date(value: object) -> str:
    return value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else "-"


def cmd_github(args: argparse.Namespace, settings: Settings) -> None:
    """Read-only GitHub lookups; shares GitHubClient and resolve_repo with the MCP tools."""
    repo = resolve_repo(settings, args.repo)
    with GitHubClient(settings) as client:
        try:
            if args.github_command == "repo":
                r = client.get_repository(repo)
                print(f"{r.full_name}  ({r.visibility or '?'}, default branch {r.default_branch})")
                print(f"  {r.description or '(no description)'}")
                print(f"  stars {r.stars} · forks {r.forks} · open issues+PRs {r.open_issues_count} · "
                      f"language {r.language or '-'} · license {r.license or '-'}")
                print(f"  topics: {', '.join(r.topics) or '-'}")
                print(f"  last push {_date(r.pushed_at)} · {r.html_url}")
            elif args.github_command == "search":
                res = client.search_issues(repo, args.text, state=args.state, kind=args.kind, limit=args.limit)
                print(f"query: {res.query}")
                print(f"{res.total_count} match(es){' (incomplete)' if res.incomplete_results else ''}, "
                      f"showing {len(res.items)}:")
                for i in res.items:
                    kind = "PR   " if i.is_pull_request else "issue"
                    labels = f" [{', '.join(i.labels)}]" if i.labels else ""
                    print(f"  #{i.number:<6} {kind} {i.state:<6} {i.title}{labels}")
                    print(f"           {i.comments} comments · updated {_date(i.updated_at)} · {i.html_url}")
            elif args.github_command == "issue":
                i = client.get_issue(repo, args.number)
                kind = "Pull request" if i.is_pull_request else "Issue"
                print(f"{kind} #{i.number}: {i.title}")
                print(f"  {i.state}{f' ({i.state_reason})' if i.state_reason else ''} · by {i.author} · "
                      f"opened {_date(i.created_at)} · labels: {', '.join(i.labels) or '-'}")
                print(f"  {i.html_url}")
                if i.note:
                    print(f"  note: {i.note}")
                print()
                print(preview(i.body, 600) or "(no body)")
                if i.body_truncated:
                    print("  [body truncated]")
                print(f"\nComments: {len(i.comments)} shown of {i.comments_count}")
                for c in i.comments:
                    print(f"  - {c.author} ({c.author_association}) {_date(c.created_at)}: {preview(c.body, 160)}")
            elif args.github_command == "prs":
                res = client.list_pull_requests(repo, state=args.state, limit=args.limit)
                print(f"{len(res.items)} {args.state} pull request(s){' (more exist)' if res.truncated else ''}:")
                for p in res.items:
                    status = "merged" if p.merged_at else ("draft" if p.draft else p.state)
                    print(f"  #{p.number:<6} {status:<7} {p.title}")
                    print(f"           {p.head_branch} -> {p.base_branch} · by {p.author} · "
                          f"updated {_date(p.updated_at)}")
            elif args.github_command == "pr":
                p = client.get_pull_request(repo, args.number)
                status = "merged" if p.merged else ("draft" if p.draft else p.state)
                print(f"PR #{p.number}: {p.title}")
                print(f"  {status} · by {p.author} · {p.head_branch} -> {p.base_branch} · "
                      f"{p.commits} commit(s), +{p.additions}/-{p.deletions}")
                print(f"  {p.html_url}")
                refs = ", ".join(f"{r.keyword} {r.repo}#{r.number}" for r in p.closing_references)
                print(f"  closes: {refs or '-'}")
                print(f"\nChanged files ({len(p.files)}{'+' if p.files_truncated else ''}):")
                for f in p.files:
                    renamed = f" (from {f.previous_filename})" if f.previous_filename else ""
                    print(f"  {f.status:<9} +{f.additions:<5} -{f.deletions:<5} {f.filename}{renamed}")
        finally:
            print(f"\n{format_rate_limit(client.last_rate_limit, client.authenticated)}")


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

    ask = add_command("ask", "Answer a question about an indexed repository, with citations")
    ask.add_argument("question", help="Your question")
    ask.add_argument("--top-k", type=int, default=None, help="Chunks to retrieve (default: RETRIEVAL_TOP_K)")
    ask.add_argument("--type", action="append", help="Only this file type, e.g. py (repeatable)")
    ask.add_argument("--show-sources", action="store_true", help="Also print every retrieved chunk")
    ask.set_defaults(handler=cmd_ask)

    github = sub.add_parser("github", help="Read-only GitHub lookups (repo, issues, pull requests)")
    github_sub = github.add_subparsers(dest="github_command", required=True)

    def add_github(name: str, help_text: str) -> argparse.ArgumentParser:
        cmd = github_sub.add_parser(name, help=help_text)
        cmd.add_argument("--repo", help="owner/name (default: GITHUB_DEFAULT_REPO); must be allowed")
        cmd.add_argument("-v", "--verbose", action="store_true", help="Show INFO logs")
        cmd.set_defaults(handler=cmd_github, path=None)
        return cmd

    add_github("repo", "Repository metadata")
    search = add_github("search", "Search issues and pull requests")
    search.add_argument("text", help="Search words, e.g. an error message")
    search.add_argument("--state", choices=["open", "closed", "all"], default="all")
    search.add_argument("--kind", choices=["issue", "pr", "any"], default="any")
    search.add_argument("--limit", type=int, default=None)
    issue = add_github("issue", "One issue with comments")
    issue.add_argument("number", type=int)
    prs = add_github("prs", "List pull requests")
    prs.add_argument("--state", choices=["open", "closed", "all"], default="open")
    prs.add_argument("--limit", type=int, default=None)
    pr = add_github("pr", "One pull request with changed files")
    pr.add_argument("number", type=int)
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
    except (ValueError, NotADirectoryError, VectorStoreError, LLMError, GitHubError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
