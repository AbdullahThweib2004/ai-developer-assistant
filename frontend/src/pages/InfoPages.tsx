import { BookOpen, Bot, CheckCircle2, CircleDot, Database, FileText, HelpCircle, History, Lock, RefreshCw, Shield, XCircle } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { FileIcon } from "../components/FileIcon";
import { GithubMark } from "../components/GithubMark";
import { Select } from "../components/Select";
import { TraceTable, TraceTotals } from "../components/Trace";
import { Badge, Button, Card, EmptyNote, PageHeader, SkeletonRows, StatusDot, Tag, cx } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { compact, seconds } from "../lib/format";
import { navigate } from "../lib/router";
import { useApp } from "../lib/store";
import type { ThemeChoice } from "../lib/theme";
import type { AuditEntry, ProjectFiles } from "../lib/types";

function Page({ children, testId }: { children: ReactNode; testId: string }) {
  return <div className="min-h-0 flex-1 overflow-y-auto" data-testid={testId}><div className="mx-auto w-full max-w-[1080px] p-6 pb-10">{children}</div></div>;
}

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="text-[12.5px] font-medium text-muted">{label}</div>
      <div className="mt-1 text-[24px] font-semibold tabular-nums tracking-[-0.02em] text-fg">{value}</div>
      {hint && <div className="mt-0.5 truncate text-[12px] text-muted">{hint}</div>}
    </Card>
  );
}

function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return <div className="mb-3 mt-8 flex items-center justify-between first:mt-0"><h2 className="text-[15px] font-semibold text-fg">{children}</h2>{right}</div>;
}

function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border py-2.5 text-[13.5px] last:border-0">
      <dt className="text-muted">{k}</dt>
      <dd className="min-w-0 truncate text-right text-fg">{v}</dd>
    </div>
  );
}

function FileTypeBars({ types }: { types: Record<string, number> }) {
  const total = Object.values(types).reduce((a, b) => a + b, 0) || 1;
  return (
    <div className="space-y-2">
      {Object.entries(types).slice(0, 6).map(([type, count]) => (
        <div key={type} className="flex items-center gap-3 text-[12.5px]">
          <span className="w-12 font-mono text-muted">.{type}</span>
          <span className="h-2 flex-1 overflow-hidden rounded-full bg-surface-3"><span className="block h-full rounded-full bg-accent/80" style={{ width: `${(100 * count) / total}%` }} /></span>
          <span className="w-8 text-right tabular-nums text-fg">{count}</span>
        </div>
      ))}
    </div>
  );
}

// ---- Overview ----------------------------------------------------------------------------------

export function OverviewPage() {
  const { projects, project, status, githubStatus, messages, repo } = useApp();
  const indexed = projects.filter((p) => p.indexed);
  const investigations = messages.filter((m) => m.answer);
  const rate = githubStatus?.rate_limit;
  return (
    <Page testId="page-overview">
      <PageHeader title="Overview" subtitle="Your indexed projects, models and this session's activity." />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Indexed projects" value={indexed.length} hint={`${projects.length - indexed.length} not indexed`} />
        <Stat label="Chunks (selected)" value={project?.indexed ? compact(project.chunks) : "–"} hint={project?.name} />
        <Stat label="Files (selected)" value={project?.indexed ? project.files : "–"} hint={project?.display_path} />
        <Stat label="Investigations" value={investigations.length} hint="in this session" />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <SectionTitle>Projects</SectionTitle>
          <Card className="divide-y divide-border">
            {projects.length === 0 && <EmptyNote title="No projects found" hint="Index a project under the allowed roots." />}
            {projects.map((p) => (
              <div key={p.path} className="flex items-center gap-3 px-4 py-3" title={p.path}>
                <StatusDot tone={p.indexed ? "success" : "warning"} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13.5px] font-medium text-fg">{p.name}</div>
                  <div className="truncate font-mono text-[11.5px] text-muted">{p.display_path ?? p.path}</div>
                </div>
                <span className="text-right text-[12.5px] text-muted">
                  {p.indexed ? <>{compact(p.chunks)} chunks · {p.files} files<br /><span className="text-[11.5px]">{p.indexed_at ? `indexed ${new Date(p.indexed_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "indexed before this run"}</span></> : "not indexed"}
                </span>
              </div>
            ))}
          </Card>
          {project?.file_types && Object.keys(project.file_types).length > 0 && (
            <>
              <SectionTitle>File types in {project.name}</SectionTitle>
              <Card className="p-4"><FileTypeBars types={project.file_types} /></Card>
            </>
          )}
        </div>

        <div>
          <SectionTitle>Models</SectionTitle>
          <Card className="px-4 py-1">
            <dl>
              <KV k="LLM" v={<span className="font-mono text-[12.5px]">{status?.llm.model ?? "not set"}</span>} />
              <KV k="Provider" v={status?.llm.provider ?? "–"} />
              <KV k="Embeddings" v={<span className="font-mono text-[12.5px]">{status?.embedding_model ?? "–"}</span>} />
              <KV k="Index model (selected)" v={<span className="font-mono text-[12.5px]">{project?.model ?? "–"}</span>} />
            </dl>
          </Card>

          <SectionTitle>GitHub</SectionTitle>
          <Card className="px-4 py-1" data-testid="overview-github">
            <dl>
              <KV k="Repository" v={<span className="font-mono text-[12.5px]">{repo ?? "not configured"}</span>} />
              <KV k="Status" v={!githubStatus ? "–" : githubStatus.reachable ? <Tag tone="success">Connected</Tag> : <Tag tone="warning">{githubStatus.configured ? "Unreachable" : "Not configured"}</Tag>} />
              <KV k="Read access" v={githubStatus?.authenticated ? "token" : "public (no token)"} />
              <KV k="Rate limit" v={rate?.remaining != null ? <span className="tabular-nums">{rate.remaining} / {rate.limit} left</span> : "–"} />
              {rate?.reset_at && <KV k="Resets" v={new Date(rate.reset_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} />}
            </dl>
          </Card>

          <SectionTitle right={investigations.length > 0 && <Button size="sm" variant="ghost" onClick={() => navigate("activity")}>View all</Button>}>Recent investigations</SectionTitle>
          <Card className="divide-y divide-border">
            {investigations.length === 0 ? <EmptyNote title="No investigations yet" hint="Ask something on the AI Assistant page." /> :
              investigations.slice(-5).reverse().map((m) => (
                <button key={m.id} type="button" onClick={() => navigate("assistant")} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-surface-2">
                  <Bot size={15} className="shrink-0 text-subtle" />
                  <span className="min-w-0 flex-1 truncate text-[13px] text-fg">{m.question}</span>
                  <span className="shrink-0 font-mono text-[11.5px] text-muted">{m.answer?.totals ? seconds(m.answer.totals.latency_ms) : ""}</span>
                </button>
              ))}
          </Card>
        </div>
      </div>
    </Page>
  );
}

// ---- Knowledge base -------------------------------------------------------------------------------

export function KnowledgePage() {
  const { projects, project, selectProject, runIndex, indexState, openCode } = useApp();
  const [files, setFiles] = useState<ProjectFiles | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    if (!project) return;
    setFiles(null);
    setError(null);
    api.projectFiles(project.path).then(setFiles).catch((e) => setError(e instanceof ApiError ? e.info.message : "Couldn't list files."));
  }, [project, project?.chunks]);
  const shown = files?.files.filter((f) => f.path.toLowerCase().includes(filter.toLowerCase())) ?? [];
  return (
    <Page testId="page-knowledge">
      <PageHeader title="Knowledge Base" subtitle="Indexed projects and the files the assistant can search."
        right={project && (
          <Button variant="primary" onClick={runIndex} disabled={indexState.running} loading={indexState.running}
            icon={project.indexed ? <RefreshCw size={14} /> : <Database size={14} />} data-testid="kb-index-button">
            {indexState.running ? "Indexing…" : project.indexed ? "Rebuild index" : "Index project"}
          </Button>
        )} />
      {indexState.running && (
        <Card className="mb-4 p-4" aria-live="polite">
          <div className="flex justify-between text-[13px]"><span className="text-fg">{indexState.label}</span><span className="font-mono tabular-nums text-muted">{Math.round(indexState.percent)}%</span></div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-3"><div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${indexState.percent}%` }} /></div>
        </Card>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {projects.map((p) => (
          <button key={p.path} type="button" onClick={() => selectProject(p.path)} title={p.path}
            className={cx("rounded-xl border bg-surface p-4 text-left shadow-card transition-[border-color,box-shadow]",
              p.path === project?.path ? "border-accent shadow-[0_0_0_3px_var(--ring)]" : "border-border hover:border-border-strong")}>
            <div className="flex items-center gap-2">
              <BookOpen size={15} className="text-subtle" />
              <span className="flex-1 truncate text-[14px] font-semibold text-fg">{p.name}</span>
              <StatusDot tone={p.indexed ? "success" : "warning"} />
            </div>
            <div className="mt-1 truncate font-mono text-[11.5px] text-muted">{p.display_path ?? p.path}</div>
            <div className="mt-3 flex gap-4 text-[12.5px] text-muted">
              {p.indexed ? <><span><b className="font-semibold text-fg">{p.files}</b> files</span><span><b className="font-semibold text-fg">{compact(p.chunks)}</b> chunks</span></> : <span className="text-warning">Not indexed</span>}
            </div>
          </button>
        ))}
      </div>

      {project && (
        <>
          <SectionTitle right={files && <span className="text-[12.5px] text-muted">{files.total_files} files allowed{files.truncated ? " (list truncated)" : ""}</span>}>
            Files in {project.name}
          </SectionTitle>
          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-center gap-3 border-b border-border p-3">
              <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter files…" aria-label="Filter files"
                className="h-9 min-w-[200px] flex-1 rounded-lg border border-border bg-surface px-3 text-[13px] text-fg outline-none focus:border-accent" />
              {files && Object.entries(files.file_types).slice(0, 5).map(([t, n]) => <Badge key={t}>.{t} · {n}</Badge>)}
            </div>
            {error ? <p className="p-4 text-[13px] text-danger">{error}</p> : !files ? <div className="p-4"><SkeletonRows rows={6} /></div> : (
              <ul className="max-h-[440px] divide-y divide-border overflow-y-auto" data-testid="kb-files">
                {shown.map((f) => (
                  <li key={f.path}>
                    <button type="button" onClick={() => openCode(f.path)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-2">
                      <FileIcon path={f.path} />
                      <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-fg">{f.path}</span>
                      <span className="text-[12px] tabular-nums text-muted">{f.size_bytes < 1024 ? `${f.size_bytes} B` : `${(f.size_bytes / 1024).toFixed(1)} KB`}</span>
                      {f.indexed ? <Tag tone="success">indexed</Tag> : <Tag>not indexed</Tag>}
                    </button>
                  </li>
                ))}
                {!shown.length && <li className="p-4 text-[13px] text-muted">No files match.</li>}
              </ul>
            )}
            {files && Object.keys(files.skipped_by_reason).length > 0 && (
              <div className="border-t border-border px-4 py-2.5 text-[12px] text-muted">
                <Lock size={11} className="mr-1 inline" /> Skipped by the loader (counts only): {Object.entries(files.skipped_by_reason).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}
              </div>
            )}
          </Card>
        </>
      )}
    </Page>
  );
}

// ---- Activity ---------------------------------------------------------------------------------------

const AUDIT_ICON: Record<string, ReactNode> = {
  created: <CheckCircle2 size={14} className="text-success" />, failed: <XCircle size={14} className="text-danger" />,
  blocked: <XCircle size={14} className="text-danger" />, unknown: <HelpCircle size={14} className="text-warning" />,
  approved: <Shield size={14} className="text-accent-text" />, cancelled: <CircleDot size={14} className="text-subtle" />,
};

export function ActivityPage() {
  const { messages } = useApp();
  const [audit, setAudit] = useState<{ entries: AuditEntry[]; error: string | null } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { api.audit().then(setAudit).catch(() => setAudit({ entries: [], error: "The audit log could not be loaded." })); }, []);
  const done = messages.filter((m) => m.status !== "running").slice().reverse();
  return (
    <Page testId="page-activity">
      <PageHeader title="Activity" subtitle="This session's investigations and the issue-creation audit log." />
      <SectionTitle>Investigations in this session</SectionTitle>
      <Card className="divide-y divide-border">
        {done.length === 0 && <EmptyNote icon={<History size={18} />} title="Nothing yet" hint="Investigations you run appear here with their traces." />}
        {done.map((m) => (
          <div key={m.id}>
            <button type="button" onClick={() => setOpen(open === m.id ? null : m.id)} aria-expanded={open === m.id}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-surface-2" data-testid="activity-item">
              <StatusDot tone={m.status === "done" ? "success" : m.status === "error" ? "danger" : "neutral"} />
              <span className="min-w-0 flex-1 truncate text-[13.5px] text-fg">{m.question}</span>
              <Badge>{m.kind === "quick" ? "quick" : m.mode ?? "auto"}</Badge>
              <span className="shrink-0 whitespace-nowrap text-right font-mono text-[11.5px] tabular-nums text-muted">
                {m.answer?.totals ? `${m.answer.totals.tool_calls} tools · ${seconds(m.answer.totals.latency_ms)}` : m.status}
              </span>
            </button>
            {open === m.id && m.answer && (
              <div className="space-y-3 bg-bg px-4 pb-4 pt-1"><TraceTotals answer={m.answer} wide /><TraceTable answer={m.answer} /></div>
            )}
          </div>
        ))}
      </Card>

      <SectionTitle>Issue-creation audit log</SectionTitle>
      <Card className="overflow-hidden" data-testid="audit-log">
        {!audit ? <div className="p-4"><SkeletonRows rows={3} /></div>
          : audit.error ? <p className="p-4 text-[13px] text-danger">{audit.error}</p>
          : !audit.entries.length ? <EmptyNote icon={<FileText size={18} />} title="No entries" hint="Approvals, cancellations and results of issue creation are recorded here (metadata only)." />
          : (
            <table className="w-full text-[12.5px]">
              <thead className="bg-surface-2 text-left text-[11px] uppercase tracking-wide text-muted">
                <tr><th className="px-4 py-2 font-medium">When</th><th className="px-2 py-2 font-medium">Action</th><th className="px-2 py-2 font-medium">Repository</th><th className="px-2 py-2 font-medium">Result</th><th className="px-4 py-2 font-medium">Draft</th></tr>
              </thead>
              <tbody>
                {audit.entries.map((e, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="px-4 py-2 tabular-nums text-muted">{new Date(e.timestamp).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</td>
                    <td className="px-2 py-2"><span className="inline-flex items-center gap-1.5 text-fg">{AUDIT_ICON[e.action]}{e.action}</span></td>
                    <td className="px-2 py-2 font-mono text-fg">{e.repo}</td>
                    <td className="px-2 py-2 text-muted">{e.issue_url ? <a href={e.issue_url} target="_blank" rel="noopener noreferrer" className="text-accent-text hover:underline">#{e.issue_number}</a> : e.error_category ?? "–"}</td>
                    <td className="px-4 py-2 font-mono text-[11.5px] text-muted">{e.draft_hash} · {e.channel}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        <p className="border-t border-border px-4 py-2 text-[11.5px] text-muted">The log never contains issue bodies, titles or tokens.</p>
      </Card>
    </Page>
  );
}

// ---- Settings -------------------------------------------------------------------------------------

export function SettingsPage() {
  const { status, theme, setTheme, githubStatus } = useApp();
  const yesNo = (v: boolean | undefined, on = "enabled", off = "disabled") => v ? <Tag tone="success">{on}</Tag> : <Tag>{off}</Tag>;
  return (
    <Page testId="page-settings">
      <PageHeader title="Settings" subtitle={<>Read-only view of the configuration in <span className="font-mono text-[12.5px]">.env</span>. Keys and tokens are never shown.</>} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <SectionTitle>Appearance</SectionTitle>
          <Card className="flex items-center justify-between gap-4 p-4">
            <div><div className="text-[13.5px] font-medium text-fg">Theme</div><div className="text-[12.5px] text-muted">Light is the default.</div></div>
            <Select<ThemeChoice> label="Theme" value={theme} onChange={setTheme} className="w-[150px]" align="right" testId="theme-select"
              options={[{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "system", label: "System" }]} />
          </Card>
          <SectionTitle>Language model</SectionTitle>
          <Card className="px-4 py-1"><dl>
            <KV k="Provider" v={status?.llm.provider ?? "–"} />
            <KV k="Model" v={<span className="font-mono text-[12.5px]">{status?.llm.model ?? "not set"}</span>} />
            <KV k="Embedding model" v={<span className="font-mono text-[12.5px]">{status?.embedding_model ?? "–"}</span>} />
            <KV k="API key" v={<span className="text-muted">configured in .env (never shown)</span>} />
          </dl></Card>
          <SectionTitle>Investigation limits</SectionTitle>
          <Card className="px-4 py-1"><dl>
            <KV k="Max tool calls" v={status?.limits.max_tool_calls ?? "–"} />
            <KV k="Max iterations" v={status?.limits.max_iterations ?? "–"} />
            <KV k="Max GitHub calls" v={status?.limits.max_github_calls ?? "–"} />
          </dl></Card>
        </div>
        <div>
          <SectionTitle>Project allowlist</SectionTitle>
          <Card className="p-4">
            <ul className="space-y-1.5">{status?.allowed_roots.map((r) => <li key={r} className="truncate font-mono text-[12.5px] text-fg" title={r}>{r}</li>)}</ul>
            <p className="mt-2 text-[12px] text-muted">MCP_ALLOWED_PROJECT_ROOTS: projects and files outside these are refused.</p>
          </Card>
          <SectionTitle>GitHub</SectionTitle>
          <Card className="px-4 py-1"><dl>
            <KV k="Read allowlist" v={<span className="font-mono text-[12.5px]">{status?.github.read_repos.join(", ") || "none"}</span>} />
            <KV k="Read access" v={status?.github.authenticated_reads ? "token" : "public (no token)"} />
            <KV k="Connection" v={githubStatus?.reachable ? <Tag tone="success">Connected</Tag> : <Tag tone="warning">{githubStatus?.configured ? "Unreachable" : "Not configured"}</Tag>} />
            <KV k="Issue creation" v={yesNo(status?.github.write_enabled, "enabled · approval required")} />
            <KV k="Write allowlist" v={<span className="font-mono text-[12.5px]">{status?.github.write_repos.join(", ") || "none"}</span>} />
          </dl></Card>
          <SectionTitle>Server</SectionTitle>
          <Card className="px-4 py-1"><dl>
            <KV k="Mode" v={status?.demo ? <Tag tone="accent">demo (no network)</Tag> : "real"} />
            <KV k="Binding" v="local only (127.0.0.1), session token required" />
            <KV k="Versions" v={<span className="font-mono text-[12px]">app {status?.versions.app ?? "?"} · python {status?.versions.python} · fastapi {status?.versions.fastapi}</span>} />
          </dl></Card>
          <div className="mt-4 flex items-center gap-2 text-[12.5px] text-muted"><GithubMark size={13} /> Writes always go through the approval drawer: typed repository name, one attempt per draft.</div>
        </div>
      </div>
    </Page>
  );
}
