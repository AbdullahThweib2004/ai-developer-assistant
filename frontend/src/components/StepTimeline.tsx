import { Check, ChevronRight, CircleAlert, Copy as CachedIcon, Loader2, Ban } from "lucide-react";
import { useState } from "react";
import { describeStep, plural, seconds, stepResult } from "../lib/format";
import type { Step } from "../lib/types";
import { cx } from "./ui";

function StatusIcon({ status }: { status: Step["status"] }) {
  if (status === "running") return <Loader2 size={13} className="animate-spin-slow text-accent" aria-label="running" />;
  if (status === "ok") return <Check size={13} className="text-success" aria-label="done" />;
  if (status === "cached") return <CachedIcon size={12} className="text-subtle" aria-label="cached" />;
  if (status === "rejected") return <Ban size={13} className="text-warning" aria-label="rejected" />;
  return <CircleAlert size={13} className="text-danger" aria-label="error" />;
}

/** Live list of tool steps; collapses to a one-line summary when the investigation finishes. */
export function StepTimeline({ steps, running, repo, sourceCount, elapsedMs, writing }: {
  steps: Step[]; running: boolean; repo?: string | null; sourceCount: number; elapsedMs: number; writing: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const open = running || expanded;
  const summary = `${plural(steps.length, "step")} · ${plural(sourceCount, "source")} · ${seconds(elapsedMs)}`;

  if (!running && steps.length === 0) return null;
  return (
    <div className="mb-3" data-testid="step-timeline">
      {!running && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="group flex items-center gap-1.5 rounded-md py-0.5 text-[12.5px] text-subtle transition-colors hover:text-fg"
        >
          <ChevronRight size={13} className={cx("transition-transform duration-200", expanded && "rotate-90")} />
          <span data-testid="step-summary">{summary}</span>
        </button>
      )}
      {open && (
        <ol className={cx("relative space-y-0.5", !running && "mt-1.5")} aria-live="polite">
          {steps.map((step) => {
            const result = step.status === "ok" ? stepResult(step.name, step.resultCount) : null;
            return (
              <li key={step.callId} className="animate-fade-up flex min-h-[26px] items-center gap-2.5 text-[13px]">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border bg-surface">
                  <StatusIcon status={step.status} />
                </span>
                <span className={cx("min-w-0 truncate", step.status === "running" ? "text-fg" : "text-muted")}>
                  {describeStep(step.name, step.args, repo)}
                  {result && <span className="text-subtle"> · {result}</span>}
                  {step.errorCategory && <span className="text-danger"> · {step.errorCategory.replace(/_/g, " ")}</span>}
                  {step.status === "cached" && <span className="text-subtle"> · cached</span>}
                </span>
                {step.durationMs != null && (
                  <span className="ml-auto shrink-0 pl-2 font-mono text-[11px] tabular-nums text-subtle">{seconds(step.durationMs)}</span>
                )}
              </li>
            );
          })}
          {running && (
            <li className="flex min-h-[26px] items-center gap-2.5 text-[13px] text-fg">
              <span className="flex h-5 w-5 items-center justify-center rounded-full border border-accent/40 bg-accent-soft">
                <span className="dot-pulse h-1.5 w-1.5 rounded-full bg-accent" />
              </span>
              <span>{writing ? "Writing answer…" : steps.length === 0 ? "Planning the investigation…" : "Thinking…"}</span>
            </li>
          )}
        </ol>
      )}
    </div>
  );
}
