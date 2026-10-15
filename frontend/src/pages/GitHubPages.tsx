import { CircleDot, ExternalLink, FileDiff, GitMerge, GitPullRequest, MessageSquare, Quote, Search } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { GithubMark } from "../components/GithubMark";
import { Markdown } from "../components/Markdown";
import { Select } from "../components/Select";
import { Badge, Card, EmptyNote, PageHeader, SkeletonRows, Tag, cx } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { navigate, useLocation } from "../lib/router";
import { useApp } from "../lib/store";
import type { IssueDetail, IssueSummary, PullDetail, PullSummary } from "../lib/types";

function date(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }) : "–";
}

/** GitHub text is external and untrusted: rendered as a quoted block (Markdown, no raw HTML). */
function ExternalQuote({ label, children }: { label: string; children: string }) {
  return (
    <blockquote className="rounded-lg border border-border border-l-[3px] border-l-border-strong bg-surface-2 px-3.5 py-2.5" data-testid="external-content">
      <div className="mb-1 flex items-center gap-1 text-[10.5px] font-medium uppercase tracking-wide text-subtle"><Quote size={10} /> {label}</div>
      {children.trim() ? <div className="text-[13px] text-fg"><Markdown>{children}</Markdown></div> : <p className="text-[13px] text-subtle">No description.</p>}
    </blockquote>
  );
}

function useRepoGuard(): { repo: string | null; guard: ReactNode | null } {
  const { status, repo } = useApp();
  if (status && !status.github.read_repos.length) {
    return { repo: null, guard: <EmptyNote icon={<GithubMark size={18} />} title="GitHub is not configured" hint="Set GITHUB_ALLOWED_REPOS (and optionally GITHUB_TOKEN) in .env, then restart." /> };
  }
  return { repo, guard: null };
}

function ListShell({ title, subtitle, controls, list, detail }: { title: string; subtitle: ReactNode; controls: ReactNode; list: ReactNode; detail: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col p-6">
      <PageHeader title={title} subtitle={subtitle} />
      <div className="flex min-h-0 flex-1 gap-4">
        <Card className="flex w-[400px] shrink-0 flex-col overflow-hidden">
          <div className="flex items-center gap-2 border-b border-border p-3">{controls}</div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">{list}</div>
        </Card>
        <Card className="min-w-0 flex-1 overflow-y-auto">{detail}</Card>
      </div>
    </div>
  );
}

function StateIcon({ state, pr, merged }: { state: string; pr?: boolean; merged?: boolean }) {
  const open = state === "open";
  const Icon = pr ? (merged ? GitMerge : GitPullRequest) : CircleDot;
  return <Icon size={15} className={cx("shrink-0", open ? "text-success" : merged ? "text-accent-text" : "text-subtle")} />;
}

// ---- Issues ----------------------------------------------------------------------------------------

export function IssuesPage() {
  const { repo, guard } = useRepoGuard();
  const { params } = useLocation();
  const selected = params.get("number") ? Number(params.get("number")) : null;
  const targetRepo = params.get("repo") ?? repo;
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [submitted, setSubmitted] = useState(params.get("q") ?? "");
  const [state, setState] = useState<"all" | "open" | "closed">("all");
  const [items, setItems] = useState<IssueSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<IssueDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    if (!targetRepo) return;
    setItems(null);
    setError(null);
    api.issues(targetRepo, submitted, state).then((r) => setItems(r.items)).catch((e) => setError(e instanceof ApiError ? e.info.message : "Couldn't load issues."));
  }, [targetRepo, submitted, state]);

  useEffect(() => {
    if (!targetRepo || selected == null) { setDetail(null); return; }
    setDetail(null);
    setDetailError(null);
    api.issue(targetRepo, selected).then(setDetail).catch((e) => setDetailError(e instanceof ApiError ? e.info.message : "Couldn't load the issue."));
  }, [targetRepo, selected]);

  if (guard) return guard;
  return (
    <ListShell
      title="GitHub Issues"
      subtitle={<>Read-only search in <span className="font-mono text-[12.5px] text-fg">{targetRepo}</span> (from the read allowlist).</>}
      controls={
        <>
          <form className="relative flex-1" onSubmit={(e) => { e.preventDefault(); setSubmitted(query.trim()); }}>
            <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search issues…" aria-label="Search issues"
              className="h-9 w-full rounded-lg border border-border bg-surface pl-8 pr-2 text-[13px] text-fg outline-none focus:border-accent" data-testid="issue-search" />
          </form>
          <Select label="Issue state" value={state} onChange={setState} className="w-[110px]" align="right"
            options={[{ value: "all", label: "All" }, { value: "open", label: "Open" }, { value: "closed", label: "Closed" }]} />
        </>
      }
      list={
        error ? <p className="p-2 text-[13px] text-danger">{error}</p>
          : !items ? <div className="p-2"><SkeletonRows rows={6} /></div>
          : !items.length ? <EmptyNote title="No issues found" hint={submitted ? "Try other words." : undefined} />
          : (
            <ul className="space-y-1" data-testid="issue-list">
              {items.map((i) => (
                <li key={i.number}>
                  <button type="button" onClick={() => navigate("issues", { number: i.number, repo: targetRepo, q: submitted || undefined })}
                    className={cx("flex w-full gap-2.5 rounded-lg p-2.5 text-left transition-colors", selected === i.number ? "bg-accent-soft" : "hover:bg-surface-2")}>
                    <span className="mt-0.5"><StateIcon state={i.state} pr={i.is_pull_request} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="line-clamp-2 text-[13.5px] font-medium leading-snug text-fg">{i.title}</span>
                      <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] text-muted">
                        <span className="font-mono">#{i.number}</span><span>·</span><span>{date(i.updated_at)}</span><span>·</span>
                        <span className="inline-flex items-center gap-1"><MessageSquare size={11} /> {i.comments}</span>
                        {i.labels.slice(0, 2).map((l) => <Badge key={l}>{l}</Badge>)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )
      }
      detail={
        selected == null ? <EmptyNote icon={<CircleDot size={18} />} title="Select an issue" hint="Shows the description with the first and the latest comments." />
          : detailError ? <EmptyNote title="Couldn't load this issue" hint={detailError} />
          : !detail ? <div className="p-6"><SkeletonRows rows={8} /></div>
          : (
            <article className="p-6" data-testid="issue-detail">
              <div className="flex items-start gap-3">
                <h2 className="flex-1 text-[18px] font-semibold leading-snug tracking-[-0.01em] text-fg">{detail.title} <span className="font-normal text-subtle">#{detail.number}</span></h2>
                <a href={detail.html_url} target="_blank" rel="noopener noreferrer" className="flex shrink-0 items-center gap-1 text-[12.5px] text-accent-text hover:underline">
                  Open on GitHub <ExternalLink size={12} />
                </a>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
                <Tag tone={detail.state === "open" ? "success" : "neutral"}><StateIcon state={detail.state} /> {detail.state}{detail.state_reason ? ` · ${detail.state_reason.replace("_", " ")}` : ""}</Tag>
                <span>by {detail.author ?? "unknown"} · opened {date(detail.created_at)} · {detail.comments_count} comments</span>
                {detail.labels.map((l) => <Badge key={l}>{l}</Badge>)}
              </div>
              <div className="mt-5 space-y-3">
                <ExternalQuote label="Issue description · external content">{detail.body}</ExternalQuote>
                {detail.comments.map((c, idx) => (
                  <div key={c.position}>
                    {detail.omitted_comments > 0 && detail.omitted_after_position != null && idx > 0 && detail.comments[idx - 1].position === detail.omitted_after_position && (
                      <div className="my-2 text-center text-[12px] text-subtle">… {detail.omitted_comments} comments not shown …</div>
                    )}
                    <ExternalQuote label={`Comment ${c.position} · ${c.author ?? "unknown"}${c.author_association && c.author_association !== "NONE" ? ` (${c.author_association.toLowerCase()})` : ""} · ${date(c.created_at)}`}>{c.body}</ExternalQuote>
                  </div>
                ))}
              </div>
            </article>
          )
      }
    />
  );
}

// ---- Pull requests -------------------------------------------------------------------------------

export function PullsPage() {
  const { repo, guard } = useRepoGuard();
  const { params } = useLocation();
  const selected = params.get("number") ? Number(params.get("number")) : null;
  const [state, setState] = useState<"open" | "closed" | "all">("all");
  const [items, setItems] = useState<PullSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<PullDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    if (!repo) return;
    setItems(null);
    setError(null);
    api.pulls(repo, state).then((r) => setItems(r.items)).catch((e) => setError(e instanceof ApiError ? e.info.message : "Couldn't load pull requests."));
  }, [repo, state]);

  useEffect(() => {
    if (!repo || selected == null) { setDetail(null); return; }
    setDetail(null);
    setDetailError(null);
    api.pull(repo, selected).then(setDetail).catch((e) => setDetailError(e instanceof ApiError ? e.info.message : "Couldn't load the pull request."));
  }, [repo, selected]);

  if (guard) return guard;
  return (
    <ListShell
      title="Pull Requests"
      subtitle={<>Read-only list for <span className="font-mono text-[12.5px] text-fg">{repo}</span>.</>}
      controls={
        <>
          <span className="flex-1 text-[13px] text-muted">{items ? `${items.length} pull request${items.length === 1 ? "" : "s"}` : "Loading…"}</span>
          <Select label="Pull request state" value={state} onChange={setState} className="w-[110px]" align="right"
            options={[{ value: "all", label: "All" }, { value: "open", label: "Open" }, { value: "closed", label: "Closed" }]} />
        </>
      }
      list={
        error ? <p className="p-2 text-[13px] text-danger">{error}</p>
          : !items ? <div className="p-2"><SkeletonRows rows={6} /></div>
          : !items.length ? <EmptyNote title={`No ${state === "all" ? "" : state} pull requests`} />
          : (
            <ul className="space-y-1" data-testid="pull-list">
              {items.map((p) => (
                <li key={p.number}>
                  <button type="button" onClick={() => navigate("pulls", { number: p.number })}
                    className={cx("flex w-full gap-2.5 rounded-lg p-2.5 text-left transition-colors", selected === p.number ? "bg-accent-soft" : "hover:bg-surface-2")}>
                    <span className="mt-0.5"><StateIcon state={p.state} pr merged={!!p.merged_at} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="line-clamp-2 text-[13.5px] font-medium leading-snug text-fg">{p.title}</span>
                      <span className="mt-0.5 block truncate text-[12px] text-muted">
                        <span className="font-mono">#{p.number}</span> · {p.author ?? "unknown"} · <span className="font-mono">{p.head_branch} → {p.base_branch}</span>
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )
      }
      detail={
        selected == null ? <EmptyNote icon={<GitPullRequest size={18} />} title="Select a pull request" hint="Shows its description and the changed files." />
          : detailError ? <EmptyNote title="Couldn't load this pull request" hint={detailError} />
          : !detail ? <div className="p-6"><SkeletonRows rows={8} /></div>
          : (
            <article className="p-6" data-testid="pull-detail">
              <div className="flex items-start gap-3">
                <h2 className="flex-1 text-[18px] font-semibold leading-snug tracking-[-0.01em] text-fg">{detail.title} <span className="font-normal text-subtle">#{detail.number}</span></h2>
                <a href={detail.html_url} target="_blank" rel="noopener noreferrer" className="flex shrink-0 items-center gap-1 text-[12.5px] text-accent-text hover:underline">
                  Open on GitHub <ExternalLink size={12} />
                </a>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
                <Tag tone={detail.merged ? "accent" : detail.state === "open" ? "success" : "neutral"}>
                  <StateIcon state={detail.state} pr merged={detail.merged} /> {detail.merged ? "merged" : detail.state}
                </Tag>
                <span className="font-mono">{detail.head_branch} → {detail.base_branch}</span>
                <span>· {detail.commits ?? "?"} commits · <span className="text-success">+{detail.additions ?? 0}</span> <span className="text-danger">−{detail.deletions ?? 0}</span></span>
              </div>
              <div className="mt-5"><ExternalQuote label="Pull request description · external content">{detail.body}</ExternalQuote></div>
              <h3 className="mb-2 mt-6 flex items-center gap-2 text-[14px] font-semibold text-fg">
                <FileDiff size={15} className="text-subtle" /> Changed files <span className="font-normal text-subtle">{detail.changed_files ?? detail.files.length}</span>
              </h3>
              <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border" data-testid="pull-files">
                {detail.files.map((f) => (
                  <li key={f.filename} className="flex items-center gap-3 px-3 py-2 text-[13px]">
                    <Badge tone={f.status === "added" ? "success" : f.status === "removed" ? "danger" : "neutral"}>{f.status}</Badge>
                    <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-fg" title={f.filename}>{f.filename}</span>
                    <span className="font-mono text-[12px] tabular-nums"><span className="text-success">+{f.additions}</span> <span className="text-danger">−{f.deletions}</span></span>
                  </li>
                ))}
              </ul>
              {detail.files_truncated && <p className="mt-2 text-[12px] text-subtle">Some files are not listed.</p>}
              {detail.closing_references.length > 0 && (
                <p className="mt-4 text-[13px] text-muted">Closes: {detail.closing_references.map((r) => `${r.repo}#${r.number}`).join(", ")}</p>
              )}
            </article>
          )
      }
    />
  );
}
