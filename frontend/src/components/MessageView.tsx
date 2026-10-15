import { AlertCircle, CircleDot, Info, PanelRight, Square } from "lucide-react";
import { useMemo } from "react";
import type { Message, Source } from "../lib/types";
import { AnswerView } from "./AnswerView";
import { EvidenceContext } from "./evidence-context";
import { StepTimeline } from "./StepTimeline";
import { Button, CopyButton } from "./ui";

const WRITE_HINT = "Issue creation is disabled. Set GITHUB_WRITE_ENABLED=true, GITHUB_WRITE_TOKEN and " +
  "GITHUB_WRITE_ALLOWED_REPOS in .env (see docs/approval.md), then restart.";

export function MessageView({ message, onOpenCitation, onShowEvidence, onPrepareIssue, writeEnabled, active, now }: {
  message: Message; onOpenCitation: (messageId: string, citeId: string) => void; onShowEvidence: (id: string) => void;
  onPrepareIssue: (id: string) => void; writeEnabled: boolean; active: boolean; now: number;
}) {
  const answer = message.answer;
  const sources = useMemo(() => {
    const map: Record<string, Source> = {};
    for (const s of [...(answer?.project_sources ?? []), ...(answer?.github_sources ?? [])]) map[s.cite_id] = s;
    return map;
  }, [answer]);
  const evidence = useMemo(() => ({
    sources, invalid: new Set(answer?.invalid_citations ?? []), open: (id: string) => onOpenCitation(message.id, id),
  }), [sources, answer, message.id, onOpenCitation]);

  const running = message.status === "running";
  const elapsed = (message.finishedAt ?? now) - message.startedAt;
  const lastStepDone = message.steps.length > 0 && message.steps.every((s) => s.status !== "running");
  const cited = Object.values(sources).filter((s) => s.cited).length;

  return (
    <article className="animate-fade-up mx-auto w-full max-w-[820px] px-6 py-5" data-testid="message">
      <div className="mb-4 flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-surface-2 px-3.5 py-2 text-[14px] leading-relaxed text-fg">
          {message.question}
        </div>
      </div>

      <EvidenceContext.Provider value={evidence}>
        {message.kind === "investigate" && (
          <StepTimeline steps={message.steps} running={running} repo={message.repo} sourceCount={cited || Object.keys(sources).length}
                        elapsedMs={elapsed} writing={running && lastStepDone} />
        )}
        {running && message.kind === "quick" && (
          <div className="mb-3 flex items-center gap-2 text-[13px] text-muted">
            <span className="dot-pulse h-1.5 w-1.5 rounded-full bg-accent" /> Retrieving sources and writing a quick answer…
          </div>
        )}
        {running && !answer && (
          <div className="space-y-2" aria-hidden>
            <div className="skeleton h-3.5 w-[42%]" />
            <div className="skeleton h-3.5 w-[88%]" />
            <div className="skeleton h-3.5 w-[76%]" />
          </div>
        )}
        {answer && <AnswerView answer={answer} />}
      </EvidenceContext.Provider>

      {message.status === "cancelled" && !answer && (
        <div className="mt-2 flex items-center gap-2 text-[13px] text-subtle"><Square size={11} /> Stopped.</div>
      )}
      {message.error && (
        <div className="mt-2 flex gap-2.5 rounded-xl border border-danger-border bg-danger-soft p-3" role="alert">
          <AlertCircle size={16} className="mt-0.5 shrink-0 text-danger" />
          <div>
            <div className="text-[13px] font-medium text-fg">{message.error.message}</div>
            {message.error.hint && <div className="mt-0.5 text-[12.5px] text-muted">{message.error.hint}</div>}
          </div>
        </div>
      )}

      {answer && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-border pt-3">
          <CopyButton text={answer.answer_markdown} label="Copy answer" />
          <Button size="sm" variant={active ? "secondary" : "ghost"} icon={<PanelRight size={13} />} onClick={() => onShowEvidence(message.id)}>
            Evidence
          </Button>
          {message.kind === "investigate" && answer.turn_id && (
            writeEnabled ? (
              <Button size="sm" variant="ghost" icon={<CircleDot size={13} />} onClick={() => onPrepareIssue(message.id)} data-testid="prepare-issue">
                Prepare GitHub issue
              </Button>
            ) : (
              <span className="flex items-center gap-1.5" title={WRITE_HINT}>
                <Button size="sm" variant="ghost" icon={<CircleDot size={13} />} disabled>Prepare GitHub issue</Button>
                <span className="flex items-center gap-1 text-[11.5px] text-subtle"><Info size={12} /> disabled · set GITHUB_WRITE_* to enable</span>
              </span>
            )
          )}
          {answer.totals && (
            <span className="ml-auto font-mono text-[11px] tabular-nums text-subtle">
              {answer.totals.input_tokens.toLocaleString()} in · {answer.totals.output_tokens.toLocaleString()} out
            </span>
          )}
        </div>
      )}
    </article>
  );
}
