import { useEffect, useRef, useState } from "react";
import { CommandPalette } from "./components/CommandPalette";
import type { ComposerHandle } from "./components/Composer";
import { ContextPanel } from "./components/ContextPanel";
import { DraftDrawer } from "./components/DraftDrawer";
import { NavSidebar } from "./components/NavSidebar";
import { Toasts } from "./components/Toasts";
import { TopBar } from "./components/TopBar";
import { SkeletonRows } from "./components/ui";
import { useLocation } from "./lib/router";
import { AppProvider, useApp } from "./lib/store";
import { AssistantPage } from "./pages/AssistantPage";
import { CodeExplorerPage } from "./pages/CodeExplorerPage";
import { IssuesPage, PullsPage } from "./pages/GitHubPages";
import { ActivityPage, KnowledgePage, OverviewPage, SettingsPage } from "./pages/InfoPages";

function useWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return width;
}

function Shell() {
  const { route } = useLocation();
  const { booted, draft, closeDraft, toasts, dismissToast, setPaletteOpen } = useApp();
  const width = useWidth();
  const narrow = width < 1200; // the context panel becomes an overlay
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.innerWidth < 1100);
  const [contextOpen, setContextOpen] = useState(() => window.innerWidth >= 1200);
  const composer = useRef<ComposerHandle>(null);

  useEffect(() => { if (narrow) setContextOpen(false); }, [narrow]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPaletteOpen]);

  const assistant = route === "assistant";
  const page = !booted ? <div className="mx-auto w-full max-w-[780px] p-8"><SkeletonRows rows={6} /></div>
    : assistant ? <AssistantPage composerRef={composer} />
    : route === "overview" ? <OverviewPage />
    : route === "knowledge" ? <KnowledgePage />
    : route === "code" ? <CodeExplorerPage />
    : route === "issues" ? <IssuesPage />
    : route === "pulls" ? <PullsPage />
    : route === "activity" ? <ActivityPage />
    : <SettingsPage />;

  return (
    <div className="h-full p-3 max-[900px]:p-0">
      <div className="flex h-full overflow-hidden rounded-2xl border border-border bg-app shadow-app max-[900px]:rounded-none max-[900px]:border-0">
        <NavSidebar route={route} collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((v) => !v)} />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar showContextButton={assistant && !contextOpen} onOpenContext={() => setContextOpen(true)} />
          <div className="relative flex min-h-0 flex-1">
            <main key={route} className="animate-fade-up flex min-w-0 flex-1 flex-col bg-bg">{page}</main>
            {assistant && contextOpen && (
              narrow
                ? <div className="absolute inset-y-0 right-0 z-40"><ContextPanel overlay onClose={() => setContextOpen(false)} /></div>
                : <ContextPanel onClose={() => setContextOpen(false)} />
            )}
          </div>
        </div>
      </div>
      {draft && <DraftDrawer initial={draft} onClose={closeDraft} />}
      <CommandPalette />
      <Toasts toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}

export default function App() {
  return <AppProvider><Shell /></AppProvider>;
}
