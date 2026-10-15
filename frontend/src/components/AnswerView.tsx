import { AlertTriangle, CheckCircle2, CircleHelp } from "lucide-react";
import { useMemo } from "react";
import { linesLabel } from "../lib/lines";
import type { Answer, Cause, Source } from "../lib/types";
import { FileIcon } from "./FileIcon";
import { Markdown } from "./Markdown";
import { Badge, Chip, cx } from "./ui";

function CauseCard({ cause, index }: { cause: Cause; index: number }) {
  const evidence = cause.kind === "evidence";
  const hypothesis = cause.kind === "hypothesis";
  const text = cause.markdown.replace(/\s*\((supported by evidence|hypothesis)[^)]*\)\s*:?/i, " ").trim();
  return (
    <li
      className={cx(
        "animate-fade-up flex gap-3 rounded-xl border bg-surface p-3.5 shadow-card",
        evidence ? "border-success-border" : hypothesis ? "border-warning-border" : "border-border",
      )}
      style={{ animationDelay: `${index * 60}ms` }}
      data-testid={`cause-${cause.rank}`}
    >
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[12px] font-semibold text-muted">
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

export interface Finding { title: string; text: string; sources: Source[] }

/** Split the "From the project" bullets into findings: a title (the first code identifier, else the
 * opening words), the cited project sources (file + line range) and the bullet text itself. */
export function keyFindings(markdown: string, sources: Record<string, Source>): Finding[] {
  const bullets: string[] = [];
  for (const line of markdown.split("\n")) {
    const start = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (start) bullets.push(start[1]);
    else if (bullets.length && line.trim()) bullets[bullets.length - 1] += ` ${line.trim()}`;
  }
  return bullets.map((text) => {
    const ids = [...new Set([...text.matchAll(/\[(S\d+)\]/g)].map((m) => m[1]))];
    const cited = ids.map((id) => sources[id]).filter((s): s is Source => !!s && s.kind === "project");
    const code = text.match(/`([^`]{2,60})`/);
    const words = text.replace(/\[[SG]\d+\]/g, "").replace(/[`*_]/g, "").trim().split(/\s+/).slice(0, 6).join(" ");
    return { title: code ? code[1] : words, text, sources: cited };
  });
}

function FindingItem({ finding, index, onOpen }: { finding: Finding; index: number; onOpen: (s: Source) => void }) {
  const unique = finding.sources.filter((s, i, all) => all.findIndex((o) => o.file_path === s.file_path && o.start_line === s.start_line) === i);
  return (
    <li className="flex gap-3" data-testid={`finding-${index + 1}`}>
      <span className="mt-[3px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[11.5px] font-semibold text-accent-text">{index + 1}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-semibold text-fg">{finding.title}</span>
          {unique.slice(0, 3).map((s) => (
            <Chip key={s.cite_id} onClick={() => onOpen(s)} aria-label={`Open ${s.file_path} ${linesLabel(s.start_line, s.end_line) ?? ""}`} title="Open in Code Explorer">
              <FileIcon path={s.file_path ?? ""} size="sm" />
              <span className="truncate">{s.file_path}</span>
              {linesLabel(s.start_line, s.end_line) && <span className="text-subtle">· {linesLabel(s.start_line, s.end_line)}</span>}
            </Chip>
          ))}
        </div>
        <div className="mt-0.5 text-muted"><Markdown>{finding.text}</Markdown></div>
      </div>
    </li>
  );
}

/** The structured answer: lead line, key findings (with files and line ranges), then the other sections. */
export function AnswerView({ answer, onOpenSource }: { answer: Answer; onOpenSource?: (s: Source) => void }) {
  const sourceMap = useMemo(() => {
    const map: Record<string, Source> = {};
    for (const s of [...answer.project_sources, ...answer.github_sources]) map[s.cite_id] = s;
    return map;
  }, [answer]);
  const sections = answer.sections.length ? answer.sections : [{ heading: "", markdown: answer.answer_markdown }];
  const lower = (h: string) => h.toLowerCase();
  const lead = sections.find((s) => lower(s.heading) === "summary" || s.heading === "");
  const projectSection = sections.find((s) => lower(s.heading).startsWith("from the project"));
  const findings = projectSection ? keyFindings(projectSection.markdown, sourceMap) : [];
  const useFindings = findings.length > 0 && findings.some((f) => f.sources.length > 0);
  const rest = sections.filter((s) => s !== lead && !(useFindings && s === projectSection));

  return (
    <div className="space-y-4 text-[14px] leading-[1.65]" data-testid="answer">
      {answer.insufficient_evidence && (
        <div className="flex items-start gap-2 rounded-lg border border-warning-border bg-warning-soft p-2.5 text-[13px] text-warning">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          The project sources don't fully answer this question.
        </div>
      )}
      {lead && <div className="animate-fade-up text-fg" data-testid="answer-lead"><Markdown>{lead.markdown}</Markdown></div>}
      {useFindings && (
        <section className="animate-fade-up" data-testid="key-findings">
          <h3 className="mb-2 text-[13px] font-semibold text-fg">Key findings</h3>
          <ol className="space-y-2.5">
            {findings.map((f, i) => <FindingItem key={i} finding={f} index={i} onOpen={(s) => onOpenSource?.(s)} />)}
          </ol>
        </section>
      )}
      {rest.map((section, i) => {
        const isCauses = lower(section.heading).startsWith("possible causes") && answer.causes.length > 0;
        const isGitHub = lower(section.heading).startsWith("from github");
        return (
          <section key={`${section.heading}-${i}`} className="animate-fade-up" style={{ animationDelay: `${i * 60}ms` }}>
            {section.heading && (
              <h3 className="mb-1.5 flex items-center gap-2 text-[13px] font-semibold text-fg">
                {section.heading}
                {isGitHub && <Badge>leads, not proof</Badge>}
                {lower(section.heading).startsWith("general knowledge") && <Badge>not from the sources</Badge>}
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
