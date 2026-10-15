import { Check, ChevronsUpDown, Database, FolderGit2, Monitor, Moon, PanelLeftClose, PanelLeftOpen, RefreshCw, Sun } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { GithubMark } from "./GithubMark";
import { compact } from "../lib/format";
import type { ThemeChoice } from "../lib/theme";
import type { Mode, Project, Status } from "../lib/types";
import { Logo } from "./Logo";
import { Badge, Button, IconButton, SectionLabel, Segmented } from "./ui";

export interface IndexState { running: boolean; percent: number; label: string }

function ProjectSwitcher({ projects, selected, onSelect }: {
  projects: Project[]; selected: Project | null; onSelect: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);
  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Switch project"
        className="flex w-full items-center gap-2.5 rounded-lg border border-border bg-surface px-2.5 py-2 text-left shadow-soft transition-colors hover:border-border-strong"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-surface-2 text-muted"><FolderGit2 size={15} /></span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-fg">{selected?.name ?? "Choose a project"}</span>
          <span className="block truncate text-[11.5px] text-subtle">
            {selected ? (selected.indexed ? `${compact(selected.chunks)} chunks · ${selected.files} files` : "Not indexed yet") : "–"}
          </span>
        </span>
        <ChevronsUpDown size={14} className="shrink-0 text-subtle" />
      </button>
      {open && (
        <div role="listbox" className="animate-fade-up absolute left-0 right-0 top-[calc(100%+4px)] z-30 rounded-lg border border-border bg-surface p-1 shadow-pop">
          {projects.map((p) => (
            <button
              key={p.path}
              type="button"
              role="option"
              aria-selected={p.path === selected?.path}
              onClick={() => { onSelect(p.path); setOpen(false); }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-surface-2"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] text-fg">{p.name}</span>
                <span className="block truncate font-mono text-[10.5px] text-subtle" title={p.path}>{p.path}</span>
              </span>
              {p.indexed ? <Badge>{compact(p.chunks)}</Badge> : <Badge tone="warning">not indexed</Badge>}
              {p.path === selected?.path && <Check size={13} className="text-accent-text" />}
            </button>
          ))}
          <form
            className="mt-1 border-t border-border p-1 pt-2"
            onSubmit={(e) => { e.preventDefault(); if (custom.trim()) { onSelect(custom.trim()); setCustom(""); setOpen(false); } }}
          >
            <input
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              placeholder="/absolute/path/to/project"
              aria-label="Project path"
              className="h-8 w-full rounded-md border border-border bg-bg-subtle px-2 font-mono text-[11.5px] text-fg outline-none focus:border-accent"
            />
            <p className="mt-1 px-0.5 text-[10.5px] text-subtle">Must be inside the allowed project roots.</p>
          </form>
        </div>
      )}
    </div>
  );
}

const THEME_NEXT: Record<ThemeChoice, ThemeChoice> = { system: "dark", dark: "light", light: "system" };

export function Sidebar({
  status, projects, selected, onSelectProject, onIndex, indexState, repo, onRepo, mode, onMode,
  collapsed, onToggle, theme, onTheme, indexedAt,
}: {
  status: Status | null; projects: Project[]; selected: Project | null; onSelectProject: (p: string) => void;
  onIndex: () => void; indexState: IndexState; repo: string | null; onRepo: (r: string | null) => void;
  mode: Mode; onMode: (m: Mode) => void; collapsed: boolean; onToggle: () => void;
  theme: ThemeChoice; onTheme: (t: ThemeChoice) => void; indexedAt?: number;
}) {
  const ThemeIcon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  if (collapsed) {
    return (
      <nav aria-label="Sidebar" className="flex h-full w-14 shrink-0 flex-col items-center gap-2 border-r border-border bg-bg-subtle py-3">
        <Logo size={26} />
        <IconButton label="Expand sidebar" onClick={onToggle} className="mt-2"><PanelLeftOpen size={16} /></IconButton>
        <div className="mt-auto">
          <IconButton label={`Theme: ${theme}`} onClick={() => onTheme(THEME_NEXT[theme])}><ThemeIcon size={15} /></IconButton>
        </div>
      </nav>
    );
  }
  const repos = status?.github.read_repos ?? [];
  return (
    <nav aria-label="Sidebar" className="flex h-full w-[272px] shrink-0 flex-col border-r border-border bg-bg-subtle">
      <div className="flex h-12 items-center gap-2.5 px-4">
        <Logo size={22} />
        <span className="text-[13.5px] font-semibold tracking-[-0.01em] text-fg">Dev Assistant</span>
        {status?.demo && <Badge tone="accent">demo</Badge>}
        <IconButton label="Collapse sidebar" onClick={onToggle} className="ml-auto"><PanelLeftClose size={15} /></IconButton>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto px-3 pb-3 pt-2">
        <section>
          <SectionLabel>Project</SectionLabel>
          <ProjectSwitcher projects={projects} selected={selected} onSelect={onSelectProject} />
          <div className="mt-2">
            <Button
              variant="secondary"
              className="w-full"
              onClick={onIndex}
              disabled={!selected || indexState.running}
              icon={indexState.running ? undefined : selected?.indexed ? <RefreshCw size={13} /> : <Database size={13} />}
              loading={indexState.running}
              data-testid="index-button"
            >
              {indexState.running ? "Indexing…" : selected?.indexed ? "Rebuild index" : "Index project"}
            </Button>
            {(indexState.running || indexState.percent > 0) && (
              <div className="mt-2" aria-live="polite">
                <div className="h-1 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-valuenow={Math.round(indexState.percent)}
                     aria-valuemin={0} aria-valuemax={100} aria-label="Indexing progress">
                  <div className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out" style={{ width: `${indexState.percent}%` }} />
                </div>
                <div className="mt-1 truncate text-[11.5px] text-subtle" data-testid="index-label">{indexState.label}</div>
              </div>
            )}
            {!indexState.running && indexState.percent === 0 && selected?.indexed && (
              <div className="mt-1.5 text-[11.5px] text-subtle">
                {indexedAt ? `Indexed ${new Date(indexedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Index ready"}
                {selected.model && <span className="block truncate font-mono text-[10.5px]">{selected.model}</span>}
              </div>
            )}
          </div>
        </section>

        <section>
          <SectionLabel>Tools</SectionLabel>
          <Segmented
            label="Investigation mode"
            value={mode}
            onChange={onMode}
            options={[{ value: "auto", label: "Auto" }, { value: "project", label: "Project" }, { value: "github", label: "GitHub" }]}
          />
          <p className="mt-1.5 text-[11.5px] leading-snug text-subtle">
            {mode === "auto" ? "Decides per question: project, GitHub, both or neither."
              : mode === "project" ? "Only searches and reads the local project." : "Only searches the GitHub repository."}
          </p>
        </section>

        <section>
          <SectionLabel>GitHub repository</SectionLabel>
          <div className="relative">
            <GithubMark size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
            <select
              value={repo ?? ""}
              onChange={(e) => onRepo(e.target.value || null)}
              disabled={mode === "project"}
              aria-label="GitHub repository"
              className="h-8 w-full appearance-none rounded-lg border border-border bg-surface pl-8 pr-7 font-mono text-[12px] text-fg outline-none transition-colors hover:border-border-strong focus:border-accent disabled:opacity-50"
            >
              <option value="">None</option>
              {repos.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <ChevronsUpDown size={13} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-subtle" />
          </div>
          <p className="mt-1.5 text-[11.5px] text-subtle">From the read allowlist. Read-only.</p>
        </section>
      </div>

      <footer className="space-y-2 border-t border-border px-3 py-3 text-[11.5px]" data-testid="status-footer">
        <div className="flex items-center justify-between gap-2">
          <span className="text-subtle">Model</span>
          <span className="truncate font-mono text-fg" title={status?.llm.model ?? ""}>{status?.llm.model ?? "not set"}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-subtle">Embeddings</span>
          <span className="truncate font-mono text-muted" title={status?.embedding_model}>{status?.embedding_model.split("/").pop()}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-subtle">Issue creation</span>
          {status?.github.write_enabled
            ? <Badge tone="warning">approval required</Badge>
            : <Badge>disabled</Badge>}
        </div>
        <div className="flex items-center justify-between pt-1">
          <span className="text-subtle">Theme</span>
          <button type="button" onClick={() => onTheme(THEME_NEXT[theme])} aria-label={`Theme: ${theme}. Click to change.`}
            className="flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-muted transition-colors hover:bg-surface-2 hover:text-fg">
            <ThemeIcon size={13} /> {theme[0].toUpperCase() + theme.slice(1)}
          </button>
        </div>
      </footer>
    </nav>
  );
}

