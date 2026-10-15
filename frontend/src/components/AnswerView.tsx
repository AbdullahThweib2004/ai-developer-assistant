import { AlertTriangle, CheckCircle2, CircleHelp } from "lucide-react";
import type { Answer, Cause } from "../lib/types";
import { Markdown } from "./Markdown";
import { Badge, cx } from "./ui";


function CauseCard({ cause, index }: { cause: Cause; index: number }) {
  const evidence = cause.kind === "evidence";
  const hypothesis = cause.kind === "hypothesis";
  const text = cause.markdown.replace(/\s*\((supported by evidence|hypothesis)[^)]*\)\s*:?/i, " ").trim();
  return (
    <li
      className={cx(
        "animate-fade-up flex gap-3 rounded-lg border bg-surface p-3 shadow-soft",
        evidence ? "border-success-border" : hypothesis ? "border-warning-border" : "border-border",
      )}
      style={{ animationDelay: `${index * 60}ms` }}
      data-testid={`cause-${cause.rank}`}
    >
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-2 font-mono text-[12px] font-semibold text-muted">
        {cause.rank}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-1">
          {evidence && <Badge tone="success"><CheckCircle2 size={11} /> Evidence</Badge>}
          {hypothesis && <Badge tone="warning"><CircleHelp size={11} /> Hypothesis</Badge>}
          {!evidence && !hypothesis && <Badge>Unlabeled</Badge>}
        </div>
        <Markdown>{text}</Markdown>
      </div>
    </li>
  );
}

/** The structured answer: one block per section, possible causes as ranked cards. */
export function AnswerView({ answer }: { answer: Answer }) {
  const sections = answer.sections.length ? answer.sections : [{ heading: "", markdown: answer.answer_markdown }];
  return (
    <div className="space-y-4" data-testid="answer">
      {answer.insufficient_evidence && (
        <div className="flex items-start gap-2 rounded-lg border border-warning-border bg-warning-soft p-2.5 text-[13px] text-warning">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          The project sources don't fully answer this question.
        </div>
      )}
      {sections.map((section, i) => {
        const isCauses = section.heading.toLowerCase().startsWith("possible causes") && answer.causes.length > 0;
        const isGitHub = section.heading.toLowerCase().startsWith("from github");
        return (
          <section key={`${section.heading}-${i}`} className="animate-fade-up" style={{ animationDelay: `${i * 70}ms` }}>
            {section.heading && (
              <h3 className="mb-1.5 flex items-center gap-2 text-[13px] font-semibold tracking-[-0.005em] text-fg">
                {section.heading}
                {isGitHub && <Badge>leads, not proof</Badge>}
                {section.heading.toLowerCase().startsWith("general knowledge") && <Badge>not from the sources</Badge>}
              </h3>
            )}
            {isCauses ? (
              <ol className="space-y-2">
                {answer.causes.map((cause, idx) => <CauseCard key={cause.rank} cause={cause} index={idx} />)}
              </ol>
            ) : (
              <Markdown>{section.markdown}</Markdown>
            )}
          </section>
        );
      })}
    </div>
  );
}
