import { CircleDot, FileCode2, GitPullRequest } from "lucide-react";
import { useRef, useState } from "react";
import { unwrap } from "../lib/format";
import { useEvidence } from "./evidence-context";
import { cx } from "./ui";

/** An inline [S1]/[G1] citation. Hover previews the source; click opens it in the Evidence panel. */
export function CitationChip({ id }: { id: string }) {
  const { sources, invalid, open } = useEvidence();
  const source = sources[id];
  const isInvalid = invalid.has(id) || !source;
  const [show, setShow] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const enter = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setShow(true), 120);
  };
  const leave = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setShow(false), 80);
  };

  if (isInvalid) {
    return (
      <span
        className="mx-0.5 cursor-help font-mono text-[11px] text-danger underline decoration-danger/70 decoration-wavy underline-offset-2"
        title={`Invalid citation: ${id} was not returned by any tool`}
        data-testid={`citation-invalid-${id}`}
      >
        {id}
      </span>
    );
  }
  const github = source.kind === "github";
  const preview = unwrap(source.excerpt).split("\n").slice(0, 6).join("\n");
  return (
    <span className="relative inline-block align-baseline" onMouseEnter={enter} onMouseLeave={leave}>
      <button
        type="button"
        onClick={() => open(id)}
        onFocus={enter}
        onBlur={leave}
        aria-label={`Open source ${id}: ${github ? `#${source.number}` : source.file_path}`}
        data-testid={`citation-${id}`}
        className={cx(
          "mx-0.5 inline-flex h-[18px] items-center rounded-[5px] border px-1 font-mono text-[10.5px] font-medium leading-none",
          "transition-colors duration-150 align-[1px]",
          github
            ? "border-border bg-surface-2 text-muted hover:border-border-strong hover:text-fg"
            : "border-transparent bg-accent-soft text-accent-text hover:border-accent/40",
        )}
      >
        {id}
      </button>
      {show && (
        <span
          role="tooltip"
          className="animate-fade-up absolute bottom-[calc(100%+6px)] left-1/2 z-40 block w-[340px] -translate-x-1/2 rounded-lg border border-border bg-surface p-2.5 text-left shadow-pop"
        >
          <span className="flex items-center gap-1.5 text-[12px] font-medium text-fg">
            {github ? (source.is_pull_request ? <GitPullRequest size={13} /> : <CircleDot size={13} />) : <FileCode2 size={13} />}
            <span className="truncate font-mono">
              {github ? `${source.repo}#${source.number}` : source.file_path}
            </span>
            {!github && source.location && <span className="ml-auto shrink-0 text-[11px] text-subtle">{source.location}</span>}
          </span>
          {github && source.title && <span className="mt-1 block text-[12px] text-muted">{source.title}</span>}
          {preview && (
            <span className="mt-1.5 block max-h-28 overflow-hidden whitespace-pre rounded-md border border-border bg-code px-2 py-1.5 font-mono text-[11px] leading-[1.5] text-muted">
              {preview}
            </span>
          )}
          <span className="mt-1.5 block text-[11px] text-subtle">Click to open in Evidence</span>
        </span>
      )}
    </span>
  );
}
