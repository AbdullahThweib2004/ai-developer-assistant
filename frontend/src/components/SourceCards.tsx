import { ChevronLeft, ChevronRight, CircleDot, ExternalLink, GitPullRequest } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { unwrap } from "../lib/format";
import { highlightLines } from "../lib/highlight";
import { linesLabel } from "../lib/lines";
import type { Source } from "../lib/types";
import { FileIcon } from "./FileIcon";
import { cx } from "./ui";

/** A short, real title for a source: the first def/class/heading in its excerpt. */
export function sourceTitle(source: Source): string {
  const text = unwrap(source.excerpt);
  const def = text.match(/^\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)/m);
  if (def) return `${def[1]} ${def[2]}`;
  const heading = text.match(/^#{1,4}\s+(.+)$/m);
  if (heading) return heading[1].trim();
  const fn = text.match(/^\s*(?:export\s+)?(?:function|const)\s+([A-Za-z_$][\w$]*)/m);
  if (fn) return fn[1];
  return source.language === "markdown" ? "Documentation excerpt" : "Code excerpt";
}

function excerptLines(source: Source, count = 3): string[] {
  const lines = unwrap(source.excerpt).split("\n");
  const first = lines.findIndex((l) => l.trim());
  return lines.slice(Math.max(0, first), Math.max(0, first) + count);
}

function ProjectCard({ source, onOpen }: { source: Source; onOpen: (s: Source) => void }) {
  const code = useMemo(() => highlightLines(excerptLines(source).join("\n"), source.language), [source]);
  const range = linesLabel(source.start_line, source.end_line);
  return (
    <button type="button" onClick={() => onOpen(source)} data-testid={`source-card-${source.cite_id}`}
      className="group flex w-[288px] shrink-0 snap-start flex-col rounded-xl border border-border bg-surface p-3 text-left shadow-card transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-px hover:border-accent/40 hover:shadow-pop">
      <div className="flex w-full items-center gap-2">
        <FileIcon path={source.file_path ?? ""} />
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-fg" title={source.file_path ?? ""}>
          {source.file_path}{range && <span className="font-normal text-muted"> · {range}</span>}
        </span>
        <ChevronRight size={14} className="shrink-0 text-subtle transition-transform group-hover:translate-x-0.5" />
      </div>
      <div className="mt-1 flex items-center gap-1.5">
        <span className="truncate text-[13px] font-semibold text-fg">{sourceTitle(source)}</span>
        <span className="ml-auto shrink-0 rounded bg-accent-soft px-1 font-mono text-[10.5px] text-accent-text">{source.cite_id}</span>
      </div>
      <pre className="mt-2 overflow-hidden rounded-lg bg-code px-2 py-1.5 font-mono text-[11px] leading-[1.55] text-muted" aria-label="Excerpt">
        {code.map((html, i) => <div key={i} className="truncate" dangerouslySetInnerHTML={{ __html: html || " " }} />)}
      </pre>
    </button>
  );
}

function GitHubCard({ source }: { source: Source }) {
  const pr = source.is_pull_request;
  return (
    <a href={source.url ?? undefined} target="_blank" rel="noopener noreferrer" data-testid={`source-card-${source.cite_id}`}
      className="group flex w-[288px] shrink-0 snap-start flex-col rounded-xl border border-border bg-surface p-3 text-left shadow-card transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-px hover:border-border-strong hover:shadow-pop">
      <div className="flex w-full items-center gap-2">
        <span className={cx(source.state === "open" ? "text-success" : "text-subtle")}>{pr ? <GitPullRequest size={15} /> : <CircleDot size={15} />}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-fg">{source.repo}#{source.number}</span>
        <ExternalLink size={13} className="shrink-0 text-subtle" />
      </div>
      <div className="mt-1 line-clamp-2 text-[13px] font-semibold leading-snug text-fg">{source.title ?? source.label}</div>
      <div className="mt-auto flex items-center gap-1.5 pt-2 text-[11.5px] text-subtle">
        <span className="rounded bg-surface-2 px-1 font-mono text-[10.5px] text-muted">{source.cite_id}</span>
        {pr ? "Pull request" : "Issue"} · {source.state ?? "unknown"} · external, untrusted
      </div>
    </a>
  );
}

/** Horizontal, scrollable row of source cards (cited first) with prev/next arrows. */
export function SourceCards({ sources, onOpen }: { sources: Source[]; onOpen: (s: Source) => void }) {
  const row = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: true, end: true });
  const ordered = useMemo(() => [...sources].sort((a, b) => Number(b.cited) - Number(a.cited)), [sources]);
  const measure = () => {
    const el = row.current;
    if (el) setEdges({ start: el.scrollLeft < 8, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 8 });
  };
  useEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [ordered]);
  if (!ordered.length) return null;
  const scroll = (dir: number) => row.current?.scrollBy({ left: dir * 300, behavior: "smooth" });
  return (
    <div className="relative" data-testid="source-cards">
      <div ref={row} onScroll={measure} className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-2 pt-1 [scrollbar-width:none]">
        {ordered.map((s) => s.kind === "github" ? <GitHubCard key={s.cite_id} source={s} /> : <ProjectCard key={s.cite_id} source={s} onOpen={onOpen} />)}
      </div>
      {!edges.start && (
        <button type="button" aria-label="Previous sources" onClick={() => scroll(-1)}
          className="absolute -left-3 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full border border-border bg-surface text-muted shadow-pop transition-colors hover:text-fg">
          <ChevronLeft size={16} />
        </button>
      )}
      {!edges.end && (
        <button type="button" aria-label="More sources" onClick={() => scroll(1)}
          className="absolute -right-3 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full border border-border bg-surface text-muted shadow-pop transition-colors hover:text-fg">
          <ChevronRight size={16} />
        </button>
      )}
    </div>
  );
}
