import { ChevronDown, FileSearch, Files, ListTree, PanelRightClose, ShieldCheck } from "lucide-react";
import { type ReactNode, useState } from "react";
import { compact, describeStep, seconds } from "../lib/format";
import { linesLabel } from "../lib/lines";
import { useApp, useNow } from "../lib/store";
import type { Message, Source, Step } from "../lib/types";
import { FileIcon } from "./FileIcon";
import { GithubMark } from "./GithubMark";
import { TraceTable, TraceTotals } from "./Trace";
import { Card, Chip, IconButton, StatusDot, Tag, cx } from "./ui";

const PROJECT_TOOLS = new Set(["search_project", "read_project_file", "list_project_files", "get_index_stats"]);
const isGitHubTool = (name: string) => name.includes("github");

export type StageStatus = "idle" | "waiting" | "running" | "completed" | "error" | "not_used";

const LABEL: Record<StageStatus, string> = {
  idle: "Idle", waiting: "Waiting", running: "Running", completed: "Completed", error: "Error", not_used: "Not used",
};
const TONE: Record<StageStatus, "success" | "accent" | "warning" | "danger" | "neutral"> = {
  idle: "neutral", waiting: "neutral", running: "accent", completed: "success", error: "danger", not_used: "neutral",
};

/** Status of one kind of tool for a message, from its live steps (SSE) and final answer. */
export function stageStatus(message: Message | null, steps: Step[], usedByQuick: boolean): StageStatus {
  if (!message) return "idle";
  if (message.kind === "quick") {
    if (!usedByQuick) return "not_used";
    return message.status === "running" ? "running" : message.status === "error" ? "error" : message.answer ? "completed" : "idle";
  }
  if (steps.some((s) => s.status === "running")) return "running";
  if (steps.length) return steps.every((s) => s.status === "error" || s.status === "rejected") ? "error" : "completed";
  return message.status === "running" ? "waiting" : "not_used";
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-[13px]">
      <span className="text-muted">{label}</span>
      <span className="min-w-0 truncate text-right text-fg">{children}</span>
    </div>
  );
}

function StageCard({ icon, title, status, children, tag, testId }: {
  icon: ReactNode; title: string; status: StageStatus; children: ReactNode; tag: ReactNode; testId: string;
}) {
  return (
    <Card className="p-4" data-testid={testId}>
      <div className="mb-3 flex items-center gap-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-2 text-muted">{icon}</span>
        <span className="flex-1 text-[14.5px] font-semibold text-fg">{title}</span>
        <StatusDot tone={TONE[status]} pulse={status === "running"} />
      </div>
      <div className="space-y-1.5">
        <Row label="Status"><span data-testid={`${testId}-status`}>{LABEL[status]}</span></Row>
        {children}
      </div>
      <div className="mt-3">{tag}</div>
    </Card>
  );
}

function Relevance({ score }: { score: number | null }) {
  if (score == null) return <span className="text-subtle">–</span>;
  const filled = Math.max(1, Math.min(4, Math.round(score * 4)));
  return (
    <span className="inline-flex items-center gap-2" title={`Best similarity ${score.toFixed(3)} (cosine)`}>
      <span className="flex gap-1" aria-hidden>
        {[0, 1, 2, 3].map((i) => <span key={i} className={cx("h-1.5 w-4 rounded-full", i < filled ? "bg-accent" : "bg-surface-3")} />)}
      </span>
      <span className="font-mono text-[11.5px] tabular-nums text-muted">{score.toFixed(2)}</span>
    </span>
  );
}

function uniqueFiles(sources: Source[]): Source[] {
  const seen = new Set<string>();
  return [...sources].sort((a, b) => Number(b.cited) - Number(a.cited)).filter((s) => {
    if (!s.file_path || seen.has(s.file_path)) return false;
    seen.add(s.file_path);
    return true;
  });
}

export function ContextPanel({ onClose, overlay }: { onClose: () => void; overlay?: boolean }) {
  const { activeMessage: message, githubStatus, status, openCode } = useApp();
  const [traceOpen, setTraceOpen] = useState(false);
  const running = message?.status === "running";
  const now = useNow(!!running);
  const steps = message?.steps ?? [];
  const projectSteps = steps.filter((s) => PROJECT_TOOLS.has(s.name));
  const githubSteps = steps.filter((s) => isGitHubTool(s.name));
  const answer = message?.answer;
  const projectSources = answer?.project_sources ?? [];
  const githubSources = answer?.github_sources ?? [];

  const ragStatus = stageStatus(message, projectSteps, true);
  const ghStatus = stageStatus(message, githubSteps, false);
  const chunks = answer ? projectSources.length
    : projectSteps.filter((s) => s.name === "search_project").reduce((n, s) => n + (s.resultCount ?? 0), 0);
  const best = projectSources.reduce<number | null>((m, s) => (s.score != null && (m == null || s.score > m) ? s.score : m), null);
  const related = answer ? githubSources.length : githubSteps.reduce((n, s) => n + (s.resultCount ?? 0), 0);
  const files = uniqueFiles(projectSources);
  const elapsed = message ? (message.finishedAt ?? now) - message.startedAt : 0;
  const rate = githubStatus?.rate_limit;

  return (
    <aside aria-label="Investigation context" data-testid="context-panel"
      className={cx("flex h-full w-[320px] shrink-0 flex-col border-l border-border bg-bg-subtle", overlay && "shadow-pop")}>
      <div className="flex h-[60px] shrink-0 items-center justify-between px-5">
        <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-fg">Investigation Context</h2>
        <IconButton label="Close investigation context" onClick={onClose}><PanelRightClose size={15} /></IconButton>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto px-4 pb-4">
        <StageCard testId="ctx-rag" icon={<FileSearch size={16} />} title="RAG Retrieval" status={ragStatus}
          tag={<Tag tone="success">Project knowledge</Tag>}>
          <Row label="Result">{message ? `${chunks} relevant chunk${chunks === 1 ? "" : "s"}` : "–"}</Row>
          <Row label="Relevance"><Relevance score={best} /></Row>
        </StageCard>

        <StageCard testId="ctx-github" icon={<GithubMark size={15} />} title="GitHub MCP" status={ghStatus}
          tag={<Tag tone="accent">External tools</Tag>}>
          <Row label="Repository"><span className="font-mono text-[12px]">{message?.repo ?? (status?.github.read_repos.length ? "–" : "not configured")}</span></Row>
          <Row label="Result">{ghStatus === "not_used" || ghStatus === "waiting" || !message ? "–" : `${related} related issue${related === 1 ? "" : "s"}/PRs`}</Row>
          <Row label="Rate limit">
            {rate?.remaining != null ? <span className="font-mono text-[12px] tabular-nums">{rate.remaining}/{rate.limit} left</span> : "–"}
          </Row>
        </StageCard>

        <Card className="p-4" data-testid="ctx-sources">
          <div className="mb-3 flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-2 text-muted"><Files size={16} /></span>
            <span className="text-[14.5px] font-semibold text-fg">Sources</span>
          </div>
          <Row label="Result">{answer ? `${files.length} file${files.length === 1 ? "" : "s"} referenced` : "–"}</Row>
          {files.length > 0 && (
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {files.map((s) => (
                <Chip key={s.file_path} onClick={() => openCode(s.file_path!, s.start_line, s.end_line)}
                  title={`${s.file_path} ${linesLabel(s.start_line, s.end_line) ?? ""}`} data-testid={`ctx-file-${s.file_path}`}>
                  <FileIcon path={s.file_path!} size="sm" />
                  <span className="max-w-[180px] truncate">{s.file_path!.split("/").pop()}</span>
                </Chip>
              ))}
            </div>
          )}
        </Card>

        <Card className="p-4" data-testid="ctx-trace">
          <div className="mb-3 flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-2 text-muted"><ListTree size={16} /></span>
            <span className="flex-1 text-[14.5px] font-semibold text-fg">Trace</span>
            {message && <span className="font-mono text-[11.5px] tabular-nums text-subtle">{seconds(elapsed)}</span>}
          </div>
          {!message ? <p className="text-[13px] text-muted">Steps appear here while an investigation runs.</p> : (
            <>
              <ol className="space-y-1">
                {steps.map((s) => (
                  <li key={s.callId} className="flex items-center gap-2 text-[12.5px]">
                    <StatusDot tone={s.status === "running" ? "accent" : s.status === "ok" || s.status === "cached" ? "success" : "danger"} pulse={s.status === "running"} />
                    <span className="min-w-0 flex-1 truncate text-muted" title={describeStep(s.name, s.args, message.repo)}>{s.name}</span>
                    <span className="font-mono text-[11px] tabular-nums text-subtle">{s.durationMs != null ? seconds(s.durationMs) : "…"}</span>
                  </li>
                ))}
                {!steps.length && <li className="text-[12.5px] text-muted">{message.kind === "quick" ? "One retrieval + one LLM call." : running ? "Planning…" : "No tool calls."}</li>}
              </ol>
              <div className="mt-2.5 flex justify-between border-t border-border pt-2 text-[12px] text-muted">
                <span>{answer?.totals?.llm_calls ?? message.llmCalls} LLM calls</span>
                <span className="font-mono tabular-nums">
                  {compact(answer?.totals?.input_tokens ?? message.tokens.input)} in · {compact(answer?.totals?.output_tokens ?? message.tokens.output)} out
                </span>
              </div>
              {answer && (
                <button type="button" onClick={() => setTraceOpen((v) => !v)} aria-expanded={traceOpen}
                  className="mt-2 flex items-center gap-1 text-[12.5px] font-medium text-accent-text hover:underline">
                  <ChevronDown size={13} className={cx("transition-transform", traceOpen && "rotate-180")} /> {traceOpen ? "Hide" : "Show"} full trace
                </button>
              )}
              {traceOpen && answer && <div className="mt-2 space-y-2"><TraceTotals answer={answer} /><TraceTable answer={answer} /></div>}
            </>
          )}
        </Card>

        <div className="rounded-xl bg-surface-2 p-4" data-testid="ctx-safety">
          <div className="flex items-center gap-2 text-[14px] font-semibold text-fg"><ShieldCheck size={16} className="text-muted" /> You're in control</div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-muted">Nothing changes in your repository without your approval.</p>
          <p className="mt-1.5 text-[12px] text-muted">
            Issue creation: {status?.github.write_enabled
              ? <span className="font-medium text-warning">enabled · approval required</span>
              : <span className="font-medium text-fg">disabled</span>}
          </p>
        </div>
      </div>
    </aside>
  );
}
