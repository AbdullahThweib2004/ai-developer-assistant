import { ChevronRight, FileCode2, Folder, FolderOpen, Lock, Quote } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { FileIcon } from "../components/FileIcon";
import { Button, Card, CopyButton, EmptyNote, SkeletonRows, cx } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { highlightLines } from "../lib/highlight";
import { linesLabel } from "../lib/lines";
import { navigate, useLocation } from "../lib/router";
import { useApp } from "../lib/store";
import type { FileView, ProjectFiles } from "../lib/types";

interface TreeNode { name: string; path: string; children: Map<string, TreeNode>; file?: { indexed: boolean } }

function buildTree(files: ProjectFiles["files"]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: new Map() };
  for (const f of files) {
    const parts = f.path.split("/");
    let node = root;
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      if (!node.children.has(part)) node.children.set(part, { name: part, path, children: new Map() });
      node = node.children.get(part)!;
      if (i === parts.length - 1) node.file = { indexed: f.indexed };
    });
  }
  return root;
}

function sorted(node: TreeNode): TreeNode[] {
  return [...node.children.values()].sort((a, b) => Number(!!a.file) - Number(!!b.file) || a.name.localeCompare(b.name));
}

function TreeItem({ node, depth, selected, open, toggle, onOpen }: {
  node: TreeNode; depth: number; selected: string | null; open: Set<string>; toggle: (p: string) => void; onOpen: (p: string) => void;
}) {
  const isOpen = open.has(node.path);
  if (node.file) {
    return (
      <li>
        <button type="button" onClick={() => onOpen(node.path)} aria-current={selected === node.path ? "true" : undefined}
          className={cx("flex h-8 w-full items-center gap-2 rounded-md pr-2 text-left text-[13px] transition-colors",
            selected === node.path ? "bg-accent-soft font-medium text-accent-text" : "text-fg hover:bg-surface-2")}
          style={{ paddingLeft: 10 + depth * 14 }} title={node.path} data-testid={`tree-file-${node.path}`}>
          <FileIcon path={node.path} size="sm" />
          <span className="min-w-0 flex-1 truncate">{node.name}</span>
          {!node.file.indexed && <span className="text-[10.5px] text-subtle">not indexed</span>}
        </button>
      </li>
    );
  }
  return (
    <li>
      <button type="button" onClick={() => toggle(node.path)} aria-expanded={isOpen}
        className="flex h-8 w-full items-center gap-1.5 rounded-md pr-2 text-left text-[13px] text-muted transition-colors hover:bg-surface-2 hover:text-fg"
        style={{ paddingLeft: 6 + depth * 14 }}>
        <ChevronRight size={13} className={cx("shrink-0 transition-transform duration-150", isOpen && "rotate-90")} />
        {isOpen ? <FolderOpen size={14} className="shrink-0 text-subtle" /> : <Folder size={14} className="shrink-0 text-subtle" />}
        <span className="truncate">{node.name}</span>
      </button>
      {isOpen && (
        <ul>{sorted(node).map((c) => <TreeItem key={c.path} node={c} depth={depth + 1} selected={selected} open={open} toggle={toggle} onOpen={onOpen} />)}</ul>
      )}
    </li>
  );
}

function Viewer({ view, start, end }: { view: FileView; start: number | null; end: number | null }) {
  const lines = useMemo(() => highlightLines(view.lines.join("\n"), view.language), [view]);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (start == null) return;
    const row = box.current?.querySelector(`[data-line="${start}"]`) as HTMLElement | null;
    if (row && box.current) box.current.scrollTop = Math.max(0, row.offsetTop - 80);
  }, [start, view]);
  const width = String(view.end_line).length;
  return (
    <div ref={box} className="relative min-h-0 flex-1 overflow-auto bg-code py-2" data-testid="file-viewer">
      <table className="w-full border-collapse font-mono text-[12.5px] leading-[1.7]">
        <tbody>
          {lines.map((html, i) => {
            const n = view.start_line + i;
            const hit = start != null && n >= start && n <= (end ?? start);
            return (
              <tr key={n} data-line={n} data-highlighted={hit || undefined} className={cx(hit && "bg-accent-soft")}>
                <td className={cx("select-none border-l-2 pl-3 pr-4 text-right align-top", hit ? "border-accent text-accent-text" : "border-transparent text-subtle")}
                  style={{ width: `${width + 3}ch` }} aria-hidden>{n}</td>
                <td className="whitespace-pre pr-6 text-fg" dangerouslySetInnerHTML={{ __html: html || " " }} />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function CodeExplorerPage() {
  const { project, toastError } = useApp();
  const { params } = useLocation();
  const file = params.get("file");
  const start = params.get("start") ? Number(params.get("start")) : null;
  const end = params.get("end") ? Number(params.get("end")) : null;
  const [files, setFiles] = useState<ProjectFiles | null>(null);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [view, setView] = useState<FileView | null>(null);
  const [viewError, setViewError] = useState<string | null>(null);
  const [loadingView, setLoadingView] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!project) return;
    setFiles(null);
    setFilesError(null);
    api.projectFiles(project.path).then(setFiles).catch((e) => setFilesError(e instanceof ApiError ? e.info.message : "Couldn't list files."));
  }, [project]);

  // Expand the folders of the open file.
  useEffect(() => {
    if (!file) return;
    const parts = file.split("/");
    setOpen((prev) => new Set([...prev, ...parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"))]));
  }, [file]);

  useEffect(() => {
    if (!project || !file) { setView(null); return; }
    let cancelled = false;
    setLoadingView(true);
    setViewError(null);
    (async () => {
      try {
        let result = await api.projectFile(project.path, file);
        // Long files are cut at the server's limit: load from just before the cited lines instead.
        if (result.truncated && start != null && start > result.end_line) result = await api.projectFile(project.path, file, Math.max(1, start - 20));
        if (!cancelled) setView(result);
      } catch (e) {
        if (!cancelled) { setView(null); setViewError(e instanceof ApiError ? e.info.message : "Couldn't read the file."); }
      } finally {
        if (!cancelled) setLoadingView(false);
      }
    })();
    return () => { cancelled = true; };
  }, [project, file, start, toastError]);

  const tree = useMemo(() => (files ? buildTree(files.files) : null), [files]);
  const openFile = (path: string) => navigate("code", { file: path });
  const toggle = (path: string) => setOpen((prev) => {
    const next = new Set(prev);
    if (next.has(path)) next.delete(path); else next.add(path);
    return next;
  });

  if (!project) return <EmptyNote icon={<FileCode2 size={18} />} title="Choose a project" />;

  return (
    <div className="flex min-h-0 flex-1 gap-4 p-6" data-testid="page-code">
      <Card className="flex w-[280px] shrink-0 flex-col overflow-hidden">
        <div className="border-b border-border px-4 py-3">
          <div className="text-[14px] font-semibold text-fg">{project.name}</div>
          <div className="text-[12px] text-muted">{files ? `${files.total_files} files the loader allows` : "Loading files…"}</div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {filesError ? <p className="p-2 text-[13px] text-danger">{filesError}</p>
            : !tree ? <div className="p-2"><SkeletonRows rows={8} /></div>
            : <ul data-testid="file-tree">{sorted(tree).map((n) => <TreeItem key={n.path} node={n} depth={0} selected={file} open={open} toggle={toggle} onOpen={openFile} />)}</ul>}
        </div>
        {files && Object.keys(files.skipped_by_reason).length > 0 && (
          <div className="border-t border-border px-4 py-2.5 text-[11.5px] text-subtle" title="Counts only: names of skipped files are never listed.">
            <Lock size={11} className="mr-1 inline" />
            Skipped: {Object.entries(files.skipped_by_reason).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}
          </div>
        )}
      </Card>

      <Card className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {!file ? (
          <EmptyNote icon={<FileCode2 size={18} />} title="Pick a file" hint="Read-only viewer. Files go through the same security rules as the assistant's read tool." />
        ) : (
          <>
            <div className="flex items-center gap-2.5 border-b border-border px-4 py-3">
              <FileIcon path={file} />
              <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-fg" data-testid="viewer-path">{file}</span>
              {start != null && <span className="rounded-md bg-accent-soft px-2 py-0.5 font-mono text-[11.5px] text-accent-text" data-testid="viewer-range">{linesLabel(start, end)}</span>}
              {view && <span className="text-[12px] text-subtle">{view.total_lines} lines</span>}
              {view && <CopyButton text={view.lines.join("\n")} label="Copy file" />}
            </div>
            {loadingView && !view ? <div className="p-4"><SkeletonRows rows={10} /></div>
              : viewError ? <EmptyNote icon={<Lock size={18} />} title="This file can't be shown" hint={viewError} />
              : view && <Viewer view={view} start={start} end={end} />}
            {view && (
              <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-2 text-[11.5px] text-subtle">
                <span className="flex items-center gap-1"><Quote size={11} /> Project content, shown as untrusted data</span>
                {view.truncated && (
                  <span className="flex items-center gap-2">
                    Showing lines {view.start_line}–{view.end_line} of {view.total_lines}
                    <Button size="sm" variant="secondary" onClick={() => api.projectFile(project.path, file, view.end_line + 1).then(setView).catch(toastError)}>
                      Next lines
                    </Button>
                  </span>
                )}
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
