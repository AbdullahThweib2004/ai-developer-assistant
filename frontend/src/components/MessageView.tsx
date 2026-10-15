import { AlertCircle, Check, CircleDot, Copy, FileCode2, Info, Square } from "lucide-react";
import { memo, useMemo, useState } from "react";
import { APP_NAME, useNow } from "../lib/store";
import type { Message, Source } from "../lib/types";
import { AnswerView } from "./AnswerView";
import { EvidenceContext } from "./evidence-context";
import { GithubMark } from "./GithubMark";
import { Logo } from "./Logo";
import { SourceCards } from "./SourceCards";
import { StepTimeline } from "./StepTimeline";
import { Button } from "./ui";

const WRITE_HINT = "Issue creation is disabled. Set GITHUB_WRITE_ENABLED=true, GITHUB_WRITE_TOKEN and " +
  "GITHUB_WRITE_ALLOWED_REPOS in .env (see docs/approval.md), then restart.";

/** The source "Open source" opens: the first cited project source, else the best-scoring one. */
export function topSource(sources: Source[]): Source | null {
  const project = sources.filter((s) => s.kind === "project" && s.file_path);
  return project.find((s) => s.cited) ?? project[0] ?? null;
}

function CopyAnswer({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button size="md" variant="secondary" icon={copied ? <Check size={14} className="text-success" /> : <Copy size={14} />}
      onClick={async () => {
        try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1400); } catch { /* unavailable */ }
      }}>
      {copied ? "Copied" : "Copy answer"}
    </Button>
  );
}

export interface MessageActions {
  onOpenSource: (s: Source) => void;
  onOpenCitation: (messageId: string, citeId: string) => void;
  onSearchGitHub: (question: string) => void;
  onDraft: (messageId: string) => void;
  onSelect: (messageId: string) => void;
}

export const MessageView = memo(function MessageView({ message, actions, writeEnabled, githubAvailable, busy }: {
  message: Message; actions: MessageActions; writeEnabled: boolean; githubAvailable: boolean; busy: boolean;
}) {
  const answer = message.answer;
  const running = message.status === "running";
  const now = useNow(running);
  const sources = useMemo(() => {
    const map: Record<string, Source> = {};
    for (const s of [...(answer?.project_sources ?? []), ...(answer?.github_sources ?? [])]) map[s.cite_id] = s;
    return map;
  }, [answer]);
  const evidence = useMemo(() => ({
    sources, invalid: new Set(answer?.invalid_citations ?? []), open: (id: string) => actions.onOpenCitation(message.id, id),
  }), [sources, answer, message.id, actions]);
  const all = useMemo(() => Object.values(sources), [sources]);
  const top = topSource(all);
  const elapsed = (message.finishedAt ?? now) - message.startedAt;
  const lastStepDone = message.steps.length > 0 && message.steps.every((s) => s.status !== "running");
  const cited = all.filter((s) => s.cited).length;

  return (
    <article id={`msg-${message.id}`} className="animate-fade-up py-4" data-testid="message" onClick={() => actions.onSelect(message.id)}>
      <div className="mb-5 flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-bubble px-4 py-2.5 text-[14px] leading-relaxed text-fg">
          {message.question}
        </div>
      </div>

      <div className="flex gap-3" data-answer-start>
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-surface shadow-soft">
          <Logo size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-2 text-[14px] font-semibold text-fg">
            {APP_NAME}
            {message.kind === "quick" && <span className="text-[11.5px] font-normal text-subtle">Quick answer</span>}
            {message.mode === "github" && message.kind === "investigate" && <span className="text-[11.5px] font-normal text-subtle">GitHub only</span>}
          </div>

          <EvidenceContext.Provider value={evidence}>
            {message.kind === "investigate" && (
              <StepTimeline steps={message.steps} running={running} repo={message.repo} sourceCount={cited || all.length}
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
            {answer && (
              <>
                <div className="mb-2 text-[14.5px] font-semibold text-fg">
                  {answer.insufficient_evidence ? "Not enough evidence in the sources" : "Here's what I found"}
                </div>
                <AnswerView answer={answer} onOpenSource={actions.onOpenSource} />
              </>
            )}
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
            <>
              <div className="mt-5 flex flex-wrap items-center gap-2" data-testid="answer-actions">
                <Button variant="primary" icon={<FileCode2 size={14} />} disabled={!top} onClick={() => top && actions.onOpenSource(top)}
                  title={top ? `Open ${top.file_path} in Code Explorer` : "No project source for this answer"} data-testid="open-source">
                  Open source
                </Button>
                <Button variant="secondary" icon={<GithubMark size={14} />} disabled={!githubAvailable || busy}
                  title={githubAvailable ? "Run a GitHub-only follow-up for this question" : "No GitHub repository is configured"}
                  onClick={() => actions.onSearchGitHub(message.question)} data-testid="search-github">
                  Search GitHub issues
                </Button>
                <CopyAnswer text={answer.answer_markdown} />
                {message.kind === "investigate" && answer.turn_id && (
                  writeEnabled ? (
                    <Button variant="secondary" icon={<CircleDot size={14} />} onClick={() => actions.onDraft(message.id)} data-testid="prepare-issue">
                      Draft GitHub issue
                    </Button>
                  ) : (
                    <span className="flex items-center gap-1.5" title={WRITE_HINT}>
                      <Button variant="secondary" icon={<CircleDot size={14} />} disabled>Draft GitHub issue</Button>
                      <span className="flex items-center gap-1 text-[11.5px] text-subtle"><Info size={12} /> disabled · set GITHUB_WRITE_*</span>
                    </span>
                  )
                )}
              </div>
              {all.length > 0 && <div className="mt-4"><SourceCards sources={all} onOpen={actions.onOpenSource} /></div>}
            </>
          )}
        </div>
      </div>
    </article>
  );
});
