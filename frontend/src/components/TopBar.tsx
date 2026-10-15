import { ChevronDown, FolderGit2, GitBranch, PanelRightOpen, Search } from "lucide-react";
import { useState } from "react";
import { compact } from "../lib/format";
import { useApp } from "../lib/store";
import { GithubMark } from "./GithubMark";
import { Select } from "./Select";
import { Badge, IconButton, Kbd, cx } from "./ui";

function ProjectSelect() {
  const { projects, project, selectProject } = useApp();
  const [custom, setCustom] = useState("");
  return (
    <Select
      label="Project"
      testId="project-select"
      value={project?.path ?? null}
      onChange={selectProject}
      menuClassName="w-[340px]"
      options={projects.map((p) => ({
        value: p.path, label: p.name, description: p.display_path ?? p.name, title: p.path,
        icon: <FolderGit2 size={15} className="shrink-0 text-subtle" />,
        right: p.indexed ? <Badge>{compact(p.chunks)} chunks</Badge> : <Badge tone="warning">not indexed</Badge>,
      }))}
      trigger={(_, open) => (
        <span className="flex h-9 items-center gap-2 rounded-lg border border-border bg-surface px-3 shadow-soft transition-colors hover:border-border-strong" title={project?.path}>
          <FolderGit2 size={15} className="text-subtle" />
          <span className="max-w-[180px] truncate text-[13.5px] font-medium text-fg">{project?.name ?? "Choose a project"}</span>
          <ChevronDown size={14} className={cx("text-subtle transition-transform duration-200", open && "rotate-180")} />
        </span>
      )}
      footer={
        <form className="mt-1 border-t border-border p-1.5 pt-2"
          onSubmit={(e) => { e.preventDefault(); if (custom.trim()) { selectProject(custom.trim()); setCustom(""); } }}>
          <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="/absolute/path/to/project" aria-label="Project path"
            onKeyDown={(e) => e.stopPropagation()}
            className="h-8 w-full rounded-md border border-border bg-bg px-2 font-mono text-[11.5px] text-fg outline-none focus:border-accent" />
          <p className="mt-1 px-0.5 text-[11px] text-subtle">Must be inside the allowed project roots.</p>
        </form>
      }
    />
  );
}

function GitHubPill() {
  const { status, repo, setRepo, githubStatus } = useApp();
  const repos = status?.github.read_repos ?? [];
  const connected = !!githubStatus?.configured && githubStatus.reachable;
  const configured = repos.length > 0;
  const rate = githubStatus?.rate_limit;
  const tooltip = !configured ? "GitHub is not configured (GITHUB_ALLOWED_REPOS)."
    : connected ? `${repo}: read access ${githubStatus?.authenticated ? "with a token" : "as public (no token)"}` +
      (rate?.remaining != null ? ` · ${rate.remaining}/${rate.limit} requests left` : "")
    : githubStatus?.message ?? "Checking GitHub…";
  const label = !configured ? "Not configured" : connected ? "Connected" : githubStatus ? "Unreachable" : "Checking…";
  const tone = !configured ? "text-muted border-border bg-surface-2" : connected
    ? "text-success border-success-border bg-success-soft" : githubStatus ? "text-warning border-warning-border bg-warning-soft" : "text-muted border-border bg-surface-2";
  return (
    <Select
      label="GitHub repository"
      testId="github-pill"
      value={repo}
      onChange={setRepo}
      align="right"
      disabled={!configured}
      menuClassName="w-[280px]"
      options={repos.map((r) => ({ value: r, label: r, icon: <GithubMark size={14} className="shrink-0 text-subtle" />, description: "Read allowlist · read-only" }))}
      trigger={(_, open) => (
        <span className={cx("flex h-9 items-center gap-2 rounded-lg border px-3 text-[13px] font-medium transition-colors", tone)} title={tooltip}>
          <GithubMark size={15} />
          <span>{label}</span>
          {configured && repo && <span className="hidden max-w-[150px] truncate font-mono text-[11.5px] font-normal opacity-80 xl:inline">{repo}</span>}
          {configured && <ChevronDown size={13} className={cx("opacity-70 transition-transform", open && "rotate-180")} />}
        </span>
      )}
    />
  );
}

export function TopBar({ onOpenContext, showContextButton }: { onOpenContext?: () => void; showContextButton?: boolean }) {
  const { projectInfo, setPaletteOpen } = useApp();
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  return (
    <header className="flex h-[60px] shrink-0 items-center gap-2.5 border-b border-border bg-app px-4">
      <ProjectSelect />
      {projectInfo?.git && (
        <span className="flex h-9 items-center gap-1.5 rounded-lg border border-border bg-surface px-3 text-[13px] text-muted shadow-soft"
          title={projectInfo.git.detached ? "Detached HEAD (read-only)" : "Current branch (read-only)"} data-testid="branch-label">
          <GitBranch size={14} className="text-subtle" />
          <span className="max-w-[140px] truncate font-medium text-fg">{projectInfo.git.branch}</span>
        </span>
      )}
      <button
        type="button"
        onClick={() => setPaletteOpen(true)}
        className="mx-auto flex h-9 w-full max-w-[420px] items-center gap-2 rounded-lg border border-border bg-surface px-3 text-[13px] text-subtle shadow-soft transition-colors hover:border-border-strong"
        aria-label="Search files, issues, and answers"
        data-testid="search-trigger"
      >
        <Search size={15} />
        <span className="flex-1 truncate text-left">Search files, issues, and answers…</span>
        <Kbd>{mac ? "⌘" : "Ctrl"}</Kbd><Kbd>K</Kbd>
      </button>
      <GitHubPill />
      {showContextButton && onOpenContext && (
        <IconButton label="Open investigation context" onClick={onOpenContext}><PanelRightOpen size={16} /></IconButton>
      )}
    </header>
  );
}
