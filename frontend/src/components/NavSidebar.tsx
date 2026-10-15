import {
  Activity, BookOpen, Database, GitPullRequest, LayoutDashboard, Monitor, Moon, PanelLeftClose, PanelLeftOpen,
  RefreshCw, Settings, Sparkles, SquareTerminal, Sun,
} from "lucide-react";
import type { ReactNode } from "react";
import { compact } from "../lib/format";
import { navigate, navigateChat, type Route } from "../lib/router";
import { APP_NAME, useApp } from "../lib/store";
import type { ThemeChoice } from "../lib/theme";
import { ConversationList } from "./ConversationList";
import { GithubMark } from "./GithubMark";
import { Logo } from "./Logo";
import { Button, IconButton, StatusDot, cx } from "./ui";

export const NAV: { route: Route; label: string; icon: ReactNode }[] = [
  { route: "overview", label: "Overview", icon: <LayoutDashboard size={17} /> },
  { route: "assistant", label: "AI Assistant", icon: <Sparkles size={17} /> },
  { route: "knowledge", label: "Knowledge Base", icon: <BookOpen size={17} /> },
  { route: "code", label: "Code Explorer", icon: <SquareTerminal size={17} /> },
  { route: "issues", label: "GitHub Issues", icon: <GithubMark size={16} /> },
  { route: "pulls", label: "Pull Requests", icon: <GitPullRequest size={17} /> },
  { route: "activity", label: "Activity", icon: <Activity size={17} /> },
  { route: "settings", label: "Settings", icon: <Settings size={17} /> },
];

const THEME_NEXT: Record<ThemeChoice, ThemeChoice> = { light: "dark", dark: "system", system: "light" };

export function ThemeButton({ compactView }: { compactView?: boolean }) {
  const { theme, setTheme } = useApp();
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  const label = `Theme: ${theme}. Click to change.`;
  if (compactView) return <IconButton label={label} onClick={() => setTheme(THEME_NEXT[theme])}><Icon size={15} /></IconButton>;
  return (
    <button type="button" onClick={() => setTheme(THEME_NEXT[theme])} aria-label={label} title={label}
      className="flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] text-muted transition-colors hover:bg-surface-2 hover:text-fg">
      <Icon size={13} /> {theme[0].toUpperCase() + theme.slice(1)}
    </button>
  );
}

function relativeTime(epochSeconds: number): string {
  const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Real index status of the selected project, with live progress while indexing. */
function WorkspaceCard() {
  const { project, indexState, runIndex } = useApp();
  const indexed = !!project?.indexed;
  const tone = indexState.running ? "accent" : indexed ? "success" : "warning";
  const title = indexState.running ? "Indexing…" : indexed ? "Workspace indexed" : "Not indexed";
  return (
    <div className="rounded-xl border border-border bg-surface p-3.5 shadow-card" data-testid="workspace-card">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13.5px] font-semibold text-fg">{title}</span>
        <StatusDot tone={tone} pulse={indexState.running} />
      </div>
      {indexState.running ? (
        <div className="mt-2.5" aria-live="polite">
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label="Indexing progress"
               aria-valuenow={Math.round(indexState.percent)} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out" style={{ width: `${indexState.percent}%` }} />
          </div>
          <div className="mt-1.5 flex justify-between gap-2 text-[12px] text-muted">
            <span className="truncate" data-testid="index-label">{indexState.label}</span>
            <span className="font-mono tabular-nums">{Math.round(indexState.percent)}%</span>
          </div>
        </div>
      ) : (
        <dl className="mt-2 space-y-0.5 text-[12.5px] text-muted">
          {project ? (
            indexed ? (
              <>
                <div className="flex justify-between"><dt>Files indexed</dt><dd className="font-medium tabular-nums text-fg">{project.files}</dd></div>
                <div className="flex justify-between"><dt>Chunks</dt><dd className="font-medium tabular-nums text-fg">{compact(project.chunks)}</dd></div>
                <div className="flex justify-between"><dt>Last indexed</dt>
                  <dd className="text-fg">{project.indexed_at ? relativeTime(project.indexed_at) : "before this run"}</dd></div>
              </>
            ) : <div>Index this project to ask questions about it.</div>
          ) : <div>Choose a project.</div>}
        </dl>
      )}
      {!indexState.running && project && (
        <Button size="sm" variant={indexed ? "secondary" : "primary"} className="mt-3 w-full" onClick={runIndex}
          icon={indexed ? <RefreshCw size={12} /> : <Database size={12} />} data-testid="index-button">
          {indexed ? "Rebuild index" : "Index project"}
        </Button>
      )}
    </div>
  );
}

/** Collapsed sidebar: index status as a dot on an icon button that (re)builds the index. */
function CompactWorkspace() {
  const { project, indexState, runIndex } = useApp();
  if (!project) return null;
  const indexed = !!project.indexed;
  const label = indexState.running ? `Indexing… ${Math.round(indexState.percent)}%`
    : indexed ? `Workspace indexed: ${project.files} files, ${project.chunks} chunks. Click to rebuild.` : "Not indexed. Click to index.";
  return (
    <button type="button" onClick={runIndex} disabled={indexState.running} aria-label={label} title={label} data-testid="index-button-compact"
      className="relative mx-auto flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-surface text-muted shadow-soft transition-colors hover:text-fg">
      <Database size={16} />
      <span className="absolute -right-1 -top-1"><StatusDot tone={indexState.running ? "accent" : indexed ? "success" : "warning"} pulse={indexState.running} /></span>
    </button>
  );
}

export function NavSidebar({ route, collapsed, onToggle }: { route: Route; collapsed: boolean; onToggle: () => void }) {
  const { activeConversationId } = useApp();
  return (
    <nav aria-label="Main" className={cx("flex h-full shrink-0 flex-col border-r border-border bg-bg-subtle transition-[width] duration-200",
      collapsed ? "w-[64px]" : "w-[248px]")}>
      <div className={cx("flex h-[60px] items-center gap-2.5", collapsed ? "justify-center" : "px-5")}>
        <Logo size={26} />
        {!collapsed && <span className="text-[16px] font-semibold tracking-[-0.015em] text-fg">{APP_NAME}</span>}
      </div>
      <ul className={cx("shrink-0 space-y-0.5 pt-2", collapsed ? "px-2" : "px-3")}>
        {NAV.map((item) => {
          const active = item.route === route;
          return (
            <li key={item.route}>
              <a
                href={item.route === "assistant" ? "/" : `/${item.route}`}
                onClick={(e) => {
                  e.preventDefault();
                  if (item.route === "assistant") navigateChat(activeConversationId);
                  else navigate(item.route);
                }}
                aria-current={active ? "page" : undefined}
                title={collapsed ? item.label : undefined}
                className={cx(
                  "relative flex h-9 items-center gap-3 rounded-lg text-[14px] transition-colors duration-150",
                  collapsed ? "justify-center" : "px-3",
                  active ? "bg-accent-soft font-medium text-accent-text" : "text-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                {active && <span className="absolute -left-3 top-2 bottom-2 w-[3px] rounded-r-full bg-accent" aria-hidden />}
                <span className={cx("shrink-0", active ? "text-accent-text" : "text-subtle")}>{item.icon}</span>
                {!collapsed && <span className="truncate">{item.label}</span>}
              </a>
            </li>
          );
        })}
      </ul>
      {collapsed ? <div className="flex-1" /> : (
        <div className="mt-3 flex min-h-[96px] flex-1 flex-col border-t border-border px-3 pt-3"><ConversationList /></div>
      )}
      <div className={cx("space-y-2 pb-4 pt-2", collapsed ? "px-2" : "px-3")}>
        {collapsed ? <CompactWorkspace /> : <WorkspaceCard />}
        <div className={cx("flex items-center", collapsed ? "flex-col gap-1" : "justify-between px-1")}>
          <ThemeButton compactView={collapsed} />
          <IconButton label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={onToggle}>
            {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
          </IconButton>
        </div>
      </div>
    </nav>
  );
}
