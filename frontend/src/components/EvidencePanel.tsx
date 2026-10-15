import { Bot, CircleDot, ExternalLink, FileCode2, GitPullRequest, PanelRightClose, Quote, Wrench } from "lucide-react";
import { useEffect, useRef } from "react";
import { compact, seconds, unwrap } from "../lib/format";
import type { Message, Source, TraceStep } from "../lib/types";
import { CodeBlock } from "./CodeBlock";
import { Badge, IconButton, Tabs, cx } from "./ui";

export type EvidenceTab = "sources" | "github" | "trace";

function ScoreBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(1, score)) * 100;
  return (
    <span className="flex items-center gap-1.5" title={`Similarity ${score.toFixed(3)}`}>
      <span className="h-1 w-14 overflow-hidden rounded-full bg-surface-3">
        <span className="block h-full rounded-full bg-accent/80" style={{ width: `${pct}%` }} />
      </span>
      <span className="font-mono text-[10.5px] tabular-nums text-subtle">{score.toFixed(2)}</span>
    </span>
  );
}

function SourceCard({ source, highlighted }: { source: Source; highlighted: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [highlighted]);
  const code = unwrap(source.excerpt);
  return (
    <div
      ref={ref}
      id={`source-${source.cite_id}`}
      data-testid={`source-card-${source.cite_id}`}
      className={cx(
        "rounded-xl border bg-surface p-3 transition-[border-color,box-shadow] duration-200",
        highlighted ? "border-accent shadow-[0_0_0_3px_var(--ring)]" : "border-border",
      )}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded-[5px] bg-accent-soft px-1 font-mono text-[10.5px] font-medium text-accent-text">{source.cite_id}</span>
        <FileCode2 size={13} className="shrink-0 text-subtle" />
        <span className="min-w-0 truncate font-mono text-[12px] text-fg" title={source.file_path ?? ""}>{source.file_path}</span>
        {source.cited ? <Badge tone="success" className="ml-auto">cited</Badge> : <Badge className="ml-auto">retrieved</Badge>}
      </div>
      <div className="mb-2 flex items-center justify-between gap-2 text-[11.5px] text-subtle">
        <span className="font-mono">{source.location}</span>
        {source.score != null && <ScoreBar score={source.score} />}
      </div>
      {code && (
        <>
          <CodeBlock code={code} language={source.language} startLine={source.start_line} maxHeight={260} />
          <div className="mt-1.5 flex items-center gap-1 text-[11px] text-subtle">
            <Quote size={11} /> Quoted project content (untrusted data)
          </div>
        </>
      )}
    </div>
  );
}

function IssueCard({ source, highlighted }: { source: Source; highlighted: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [highlighted]);
  const pr = source.is_pull_request;
  const excerpt = unwrap(source.excerpt);
  return (
    <div
      ref={ref}
      id={`source-${source.cite_id}`}
      data-testid={`source-card-${source.cite_id}`}
      className={cx(
        "rounded-xl border bg-surface p-3 transition-[border-color,box-shadow] duration-200",
        highlighted ? "border-accent shadow-[0_0_0_3px_var(--ring)]" : "border-border",
      )}
    >
      <div className="flex items-start gap-2.5">
        <span className={cx("mt-0.5", source.state === "open" ? "text-success" : "text-subtle")}>
          {pr ? <GitPullRequest size={15} /> : <CircleDot size={15} />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-[11.5px] text-subtle">
            <span className="rounded-[5px] bg-surface-2 px-1 font-mono text-[10.5px] text-muted">{source.cite_id}</span>
            <span className="font-mono">{source.repo}#{source.number}</span>
            {source.state && <Badge tone={source.state === "open" ? "success" : "neutral"}>{pr && source.state === "closed" ? "merged/closed" : source.state}</Badge>}
            {source.cited && <Badge tone="accent">cited</Badge>}
          </div>
          <a
            href={source.url ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
            className="group mt-1 flex items-start gap-1 text-[13px] font-medium leading-snug text-fg hover:text-accent-text"
          >
            <span>{source.title ?? source.label}</span>
            <ExternalLink size={12} className="mt-1 shrink-0 opacity-0 transition-opacity group-hover:opacity-100" />
          </a>
          {source.labels.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {source.labels.map((l) => <Badge key={l}>{l}</Badge>)}
            </div>
          )}
          {excerpt && (
            <blockquote className="mt-2 border-l-2 border-border-strong pl-2.5 text-[12px] leading-relaxed text-muted">
              <span className="mb-0.5 flex items-center gap-1 text-[10.5px] uppercase tracking-wide text-subtle">
                <Quote size={10} /> External content from GitHub
              </span>
              <span className="line-clamp-4">{excerpt}</span>
            </blockquote>
          )}
        </div>
      </div>
    </div>
  );
}

function TraceView({ message }: { message: Message }) {
  const trace: TraceStep[] = message.answer?.trace ?? [];
  const totals = message.answer?.totals;
  return (
    <div className="space-y-3">
      {totals && (
        <div className="grid grid-cols-2 gap-2">
          {[
            ["Tool calls", String(totals.tool_calls)],
            ["LLM calls", String(totals.llm_calls)],
            ["Tokens", `${compact(totals.input_tokens)} / ${compact(totals.output_tokens)}`],
            ["Duration", seconds(totals.latency_ms)],
          ].map(([k, v]) => (
            <div key={k} className="rounded-lg border border-border bg-surface px-2.5 py-2">
              <div className="text-[10.5px] uppercase tracking-wide text-subtle">{k}</div>
              <div className="mt-0.5 font-mono text-[13px] tabular-nums text-fg">{v}</div>
            </div>
          ))}
        </div>
      )}
      {trace.length === 0 ? (
        <p className="text-[13px] text-subtle">The trace appears when the investigation finishes.</p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border bg-surface">
          <table className="w-full table-fixed text-[12px]" data-testid="trace-table">
            <thead className="bg-surface-2 text-left text-[10.5px] uppercase tracking-wide text-subtle">
              <tr>
                <th className="w-7 px-2 py-1.5 font-medium">#</th>
                <th className="px-2 py-1.5 font-medium">Step</th>
                <th className="w-[84px] px-2 py-1.5 font-medium">Status</th>
                <th className="w-[58px] px-2 py-1.5 text-right font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {trace.map((t) => (
                <tr key={t.step} className="border-t border-border align-top">
                  <td className="px-2 py-1.5 font-mono text-subtle">{t.step}</td>
                  <td className="min-w-0 px-2 py-1.5">
                    <div className="flex items-center gap-1.5 text-fg">
                      {t.kind === "llm" ? <Bot size={12} className="text-accent-text" /> : <Wrench size={12} className="text-subtle" />}
                      <span className="truncate font-mono">{t.kind === "llm" ? "LLM" : t.name}</span>
                    </div>
                    <div className="mt-0.5 truncate font-mono text-[11px] text-subtle" title={JSON.stringify(t.args ?? {})}>
                      {t.kind === "llm"
                        ? `${compact(t.input_tokens)} in · ${compact(t.output_tokens)} out · ${t.tool_calls_requested ?? 0} tool calls`
                        : `${JSON.stringify(t.args ?? {})}${t.result_chars != null ? ` → ${compact(t.result_chars)} chars` : ""}`}
                    </div>
                  </td>
                  <td className="px-2 py-1.5">
                    <Badge tone={t.status === "ok" || t.status === "answer" || t.status === "tool_calls" ? "neutral"
                      : t.status === "error" ? "danger" : t.status === "rejected" ? "warning" : "neutral"}>
                      {t.error_category ?? t.status.replace("_", " ")}
                    </Badge>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-subtle">{seconds(t.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[11px] text-subtle">The trace records metadata only: no file, issue or prompt content.</p>
    </div>
  );
}

export function EvidencePanel({ message, tab, onTab, highlighted, onClose, width, onResize }: {
  message: Message | null; tab: EvidenceTab; onTab: (t: EvidenceTab) => void; highlighted: string | null;
  onClose: () => void; width: number; onResize: (w: number) => void;
}) {
  const projectSources = message?.answer?.project_sources ?? [];
  const githubSources = message?.answer?.github_sources ?? [];
  const startDrag = (e: React.PointerEvent) => {
    const startX = e.clientX;
    const startW = width;
    const move = (ev: PointerEvent) => onResize(Math.max(320, Math.min(720, startW + (startX - ev.clientX))));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return (
    <aside
      aria-label="Evidence"
      className="relative flex h-full shrink-0 flex-col border-l border-border bg-bg-subtle"
      style={{ width }}
      data-testid="evidence-panel"
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize evidence panel"
        onPointerDown={startDrag}
        className="absolute -left-1 top-0 z-10 h-full w-2 cursor-col-resize hover:bg-accent/20"
      />
      <div className="flex h-12 items-center justify-between border-b border-border px-3">
        <Tabs
          label="Evidence"
          value={tab}
          onChange={onTab}
          tabs={[
            { value: "sources", label: "Sources", count: projectSources.length },
            { value: "github", label: "GitHub", count: githubSources.length },
            { value: "trace", label: "Trace" },
          ]}
        />
        <IconButton label="Close evidence panel" onClick={onClose}><PanelRightClose size={15} /></IconButton>
      </div>
      <div className="flex-1 space-y-2.5 overflow-y-auto p-3">
        {!message?.answer && (
          <p className="pt-6 text-center text-[13px] text-subtle">
            {message?.status === "running" ? "Sources appear when the answer is ready." : "Ask a question to see its evidence here."}
          </p>
        )}
        {message?.answer && tab === "sources" && (projectSources.length
          ? projectSources.map((s) => <SourceCard key={s.cite_id} source={s} highlighted={highlighted === s.cite_id} />)
          : <p className="text-[13px] text-subtle">No project sources for this answer.</p>)}
        {message?.answer && tab === "github" && (githubSources.length
          ? githubSources.map((s) => <IssueCard key={s.cite_id} source={s} highlighted={highlighted === s.cite_id} />)
          : <p className="text-[13px] text-subtle">No GitHub sources for this answer.</p>)}
        {message?.answer && tab === "trace" && <TraceView message={message} />}
      </div>
    </aside>
  );
}
