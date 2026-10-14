"""Command-line entry point.

Usage:
    python -m app.cli ingest --path ./repo [--output chunks.jsonl] [-v]
    python -m app.cli index  --path ./repo [--rebuild]
    python -m app.cli search --path ./repo "how is auth done?" [--top-k 5] [--type py]
    python -m app.cli stats  --path ./repo
    python -m app.cli ask    --path ./repo "How does login work?" [--top-k 5] [--type py] [--show-sources]
    python -m app.cli github repo|search|issue|prs|pr ... [--repo owner/name]   (read-only)
    python -m app.cli investigate --path ./repo [--repo owner/name] [--mode auto|project|github] [--trace] "question"
    python -m app.cli chat --path ./repo [--repo owner/name] [--mode ...]
    python -m app.cli investigate ... --propose-issue [--issue-repo owner/name]   (human approval required)
    python -m app.cli github draft-issue --repo owner/name --title "..." --body-file body.md
"""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from app.actions.approval import ApprovalError, IssueApprovalService, SubmissionResult
from app.actions.issue_draft import IssueDraft, manual_draft
from app.config import Settings, get_settings
from app.integrations.github_client import GitHubClient, resolve_repo
from app.integrations.github_errors import GitHubError
from app.integrations.github_models import RateLimitStatus
from app.ingestion.models import Chunk
from app.ingestion.pipeline import PipelineResult, run_pipeline
from app.llm.base import LLMError
from app.orchestration.assistant import (
    InvestigationResult,
    Investigator,
    OrchestrationConfigError,
    SessionConfig,
    build_tool_llm,
    draft_from_answer,
    unwrap_untrusted,
)
from app.integrations.github_write_client import write_allowed_repos
from app.orchestration.tool_backends import InProcessBackend, MCPClientBackend, ToolBackend
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
                    print(f"  {c.position:>3}. {c.author} ({c.author_association}) {_date(c.created_at)}: "
                          f"{preview(c.body, 160)}")
                    if i.omitted_comments and c.position == i.omitted_after_position:
                        print(f"       ... {i.omitted_comments} comment(s) omitted ...")
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


def format_investigation(result: InvestigationResult, show_trace: bool = False) -> str:
    lines = [result.answer_text, ""]
    if result.invalid_citations:
        lines.append(f"! Invalid citations (not returned by any tool): {', '.join(result.invalid_citations)}")
    if result.limits_hit:
        lines.append(f"! Limits reached: {', '.join(result.limits_hit)} (answered with the evidence gathered)")
    if result.github_disabled:
        lines.append("! GitHub calls were disabled for this question after a rate limit")

    def section(title: str, sources: list) -> None:  # type: ignore[type-arg]
        cited = [s for s in sources if s.cited]
        if not sources:
            return
        lines.append(f"{title} ({len(cited)} cited of {len(sources)} found):")
        for src in cited if not show_trace else sources:
            mark = "" if src.cited else "  (not cited)"
            if src.kind == "project":
                score = f", score {src.score:.3f}" if src.score is not None else ""
                lines.append(f"  [{src.cite_id}] {src.file_path} ({src.location}{score}){mark}")
            else:
                kind = "PR" if src.is_pull_request else "issue"
                lines.append(f"  [{src.cite_id}] {kind} {unwrap_untrusted(src.label)[:110]}{mark}")
                lines.append(f"        {src.url}")

    section("Project sources", result.project_sources)
    section("GitHub sources", result.github_sources)
    lines.append("")
    lines.append(f"Tools: {result.tool_calls} executed, {result.cached_calls} cached, {result.rejected_calls} rejected"
                 f" | mode {result.mode} | repo {result.repo or '-'}")
    lines.append(f"LLM: {result.llm_calls} calls, {result.input_tokens} in / {result.output_tokens} out tokens"
                 f" | {result.model} ({result.provider}) | prompt {result.prompt_version}")
    lat = result.latency
    lines.append(f"Latency: total {lat.total_ms / 1000:.1f}s (LLM {lat.llm_ms / 1000:.1f}s, tools {lat.tools_ms / 1000:.1f}s)")
    if show_trace:
        lines.append("")
        lines.append("Trace:")
        for t in result.trace:
            if t.kind == "llm":
                lines.append(f"  {t.step:>2}. [it {t.iteration}] LLM {t.status:<10} {t.duration_ms / 1000:5.1f}s "
                             f"tokens {t.input_tokens}/{t.output_tokens} tool_calls={t.tool_calls_requested}")
            else:
                err = f" category={t.error_category}" if t.error_category else ""
                size = f" result={t.result_chars} chars" if t.result_chars is not None else ""
                lines.append(f"  {t.step:>2}. [it {t.iteration}] tool {t.name:<26} {t.status:<8} "
                             f"{t.duration_ms / 1000:5.2f}s args={t.args}{size}{err}")
    return "\n".join(lines)


# ---- human approval (issue creation) ------------------------------------------------------


def format_draft(draft: IssueDraft) -> str:
    bar = "=" * 72
    lines = [bar, f"DRAFT GITHUB ISSUE (nothing has been created)   draft {draft.draft_id}", bar,
             f"Destination repository: {draft.repo}", f"Title: {draft.title}",
             f"Labels: {', '.join(draft.labels) or '-'}", f"Draft hash: {draft.draft_hash[:16]}…", "-" * 72,
             draft.body.rstrip(), "-" * 72]
    for warning in draft.warnings:
        lines.append(f"note: {warning}")
    for error in draft.errors:
        lines.append(f"BLOCKED: {error}")
    return "\n".join(lines)


def format_submission(result: SubmissionResult) -> str:
    if result.status == "created":
        return f"Created issue #{result.number}: {result.html_url}"
    lines = [f"Issue NOT confirmed as created (status: {result.status}, {result.error_category or '-'}): {result.message}"]
    for match in result.possible_matches:
        lines.append(f"  possible match: #{match.number} {match.title} {match.html_url}")
    return "\n".join(lines)


def approval_prompt(service: IssueApprovalService, draft: IssueDraft, ask=input) -> SubmissionResult | None:  # type: ignore[no-untyped-def]
    """Show the draft and create it only if the human types the repository name exactly."""
    print(format_draft(draft))
    if not draft.valid:
        print("This draft can't be approved until the problems above are fixed. Nothing was created.")
        service.cancel(draft.draft_id)
        return None
    typed = ask(f"\nTo create this issue, type the repository name ({draft.repo}). Anything else cancels: ")
    try:
        approval = service.approve(draft.draft_id, typed)
    except ApprovalError as exc:
        print(f"{exc} Nothing was created.")
        return None
    result = service.submit(approval.approval_id)
    if result.status == "needs_confirmation":
        print(f"\n{result.message}")
        for issue in result.similar_issues:
            print(f"  #{issue.number} ({issue.similarity:.0%} similar) {issue.title}\n      {issue.html_url}")
        again = ask("Type 'create anyway' to create it regardless. Anything else cancels: ")
        if again.strip() != "create anyway":
            service.cancel(draft.draft_id)
            print("Cancelled. Nothing was created.")
            return None
        result = service.submit(approval.approval_id, confirm_similar=True)
    print(format_submission(result))
    return result


def default_issue_repo(settings: Settings, explicit: str | None) -> str:
    if explicit:
        return explicit
    repos = write_allowed_repos(settings)
    if len(repos) != 1:
        raise OrchestrationConfigError("Pass --issue-repo (GITHUB_WRITE_ALLOWED_REPOS must name exactly one default).")
    return repos[0].full_name


def open_investigation(args: argparse.Namespace, settings: Settings) -> tuple[Investigator, ToolBackend]:
    """The investigator for the session given on the command line (tools via a real MCP session by default)."""
    project = settings.project_root
    llm = build_tool_llm(settings)
    backend: ToolBackend = (
        InProcessBackend(settings, project) if args.backend == "inprocess" else MCPClientBackend(settings, project)
    )
    try:
        issue_repo = default_issue_repo(settings, args.issue_repo) if getattr(args, "propose_issue", False) else None
        session = SessionConfig(project_path=project, repo=args.repo, mode=args.mode, issue_repo=issue_repo)
        return Investigator(settings, llm, backend, session), backend
    except Exception:
        backend.close()
        raise


def cmd_investigate(args: argparse.Namespace, settings: Settings) -> None:
    investigator, backend = open_investigation(args, settings)
    try:
        result = investigator.ask(args.question)
    finally:
        backend.close()
    print(format_investigation(result, show_trace=args.trace))
    if not args.propose_issue:
        return
    service = IssueApprovalService(settings, channel="cli", project_root=investigator.project)
    draft = result.issue_draft
    if draft is None:
        answer = input("\nNo issue draft was prepared. Prepare one from this answer? [y/N] ")
        if answer.strip().lower() != "y":
            print("No draft. Nothing was created.")
            return
        draft = draft_from_answer(settings, result, investigator.issue_repo or "", investigator.project)
    approval_prompt(service, service.add_draft(draft))


def cmd_draft_issue(args: argparse.Namespace, settings: Settings) -> None:
    body = args.body_file.read_text(encoding="utf-8")
    repo = default_issue_repo(settings, args.repo)
    service = IssueApprovalService(settings, channel="cli", project_root=settings.project_root)
    draft = service.add_draft(manual_draft(settings, repo, args.title, body, args.label or []))
    approval_prompt(service, draft)


def cmd_chat(args: argparse.Namespace, settings: Settings) -> None:
    investigator, backend = open_investigation(args, settings)
    print(f"Investigating {investigator.project.name} (mode {investigator.mode}, repo {investigator.repo or '-'}). "
          "Commands: /reset clears memory, /exit quits.")
    try:
        while True:
            try:
                question = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not question:
                continue
            if question in ("/exit", "/quit"):
                break
            if question == "/reset":
                investigator.reset()
                print("Memory cleared.")
                continue
            try:
                print("\n" + format_investigation(investigator.ask(question), show_trace=args.trace))
            except (LLMError, OrchestrationConfigError) as exc:
                print(f"Error: {exc}")
    finally:
        backend.close()


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
    draft_issue = github_sub.add_parser("draft-issue", help="Draft an issue and create it after your approval")
    draft_issue.add_argument("--repo", help="Destination owner/name (must be in GITHUB_WRITE_ALLOWED_REPOS)")
    draft_issue.add_argument("--title", required=True)
    draft_issue.add_argument("--body-file", type=Path, required=True, help="Markdown file with the issue body")
    draft_issue.add_argument("--label", action="append", help="Label (repeatable)")
    draft_issue.add_argument("-v", "--verbose", action="store_true")
    draft_issue.set_defaults(handler=cmd_draft_issue, path=None)
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

    def add_session_args(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--repo", help="GitHub repo owner/name for this session (default: GITHUB_DEFAULT_REPO)")
        cmd.add_argument("--mode", choices=["auto", "project", "github"], default="auto",
                         help="Which tools the assistant may use (default: auto)")
        cmd.add_argument("--trace", action="store_true", help="Print the investigation trace")
        cmd.add_argument("--backend", choices=["mcp", "inprocess"], default="mcp",
                         help="Run tools through the real MCP server (default) or in-process")

    investigate = add_command("investigate", "Investigate a question with project and GitHub tools")
    investigate.add_argument("question")
    add_session_args(investigate)
    investigate.add_argument("--propose-issue", action="store_true",
                             help="Let the assistant draft a GitHub issue; you review it and approve or cancel")
    investigate.add_argument("--issue-repo", help="Destination for the draft (default: the only write-allowed repo)")
    investigate.set_defaults(handler=cmd_investigate)

    chat = add_command("chat", "Interactive multi-turn investigation")
    add_session_args(chat)
    chat.set_defaults(handler=cmd_chat)
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
    except (ValueError, NotADirectoryError, VectorStoreError, LLMError, GitHubError, OrchestrationConfigError,
            ApprovalError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
