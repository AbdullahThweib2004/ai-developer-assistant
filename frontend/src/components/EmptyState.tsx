import { CircleDot, FileSearch, ShieldCheck } from "lucide-react";
import { Logo } from "./Logo";

export const EXAMPLES = [
  "Why do requests to /users/me return 401 even though I send a valid bearer token?",
  "Where does the API read the JWT signing secret from?",
  "How does the server parse the Authorization header?",
  "Are there FastAPI issues about HTTPBearer returning 403 instead of 401?",
];

export function EmptyState({ projectName, onPick }: { projectName?: string; onPick: (q: string) => void }) {
  return (
    <div className="animate-fade-up mx-auto flex w-full max-w-[720px] flex-col items-center px-6 pt-[12vh] text-center">
      <Logo size={40} />
      <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.02em] text-fg">Investigate your codebase</h1>
      <p className="mt-2 max-w-[520px] text-[14px] leading-relaxed text-muted">
        Ask about {projectName ? <span className="font-mono text-[13px] text-fg">{projectName}</span> : "a project"}, an error, or a bug.
        The assistant searches the code, checks related GitHub issues, and answers with sources you can inspect.
      </p>
      <div className="mt-5 flex flex-wrap justify-center gap-x-5 gap-y-2 text-[12.5px] text-subtle">
        <span className="flex items-center gap-1.5"><FileSearch size={14} /> Cited project sources</span>
        <span className="flex items-center gap-1.5"><CircleDot size={14} /> GitHub issues as leads</span>
        <span className="flex items-center gap-1.5"><ShieldCheck size={14} /> Read-only; issues only with your approval</span>
      </div>
      <div className="mt-8 grid w-full grid-cols-1 gap-2 sm:grid-cols-2">
        {EXAMPLES.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className="rounded-xl border border-border bg-surface p-3 text-left text-[13px] leading-snug text-muted shadow-soft transition-[border-color,color,transform] duration-150 hover:-translate-y-px hover:border-border-strong hover:text-fg"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
