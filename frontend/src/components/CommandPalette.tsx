import { Bot, CircleDot, CornerDownLeft, FileCode2, Search } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { linesLabel } from "../lib/lines";
import { navigate, navigateChat } from "../lib/router";
import { useApp } from "../lib/store";
import type { IssueSummary, SearchHit } from "../lib/types";
import { FileIcon } from "./FileIcon";
import { NAV } from "./NavSidebar";
import { Kbd, cx } from "./ui";

interface Item { id: string; group: string; icon: ReactNode; title: string; detail?: string; run: () => void }

/** Ctrl/Cmd+K: searches the project index, GitHub issues in the selected repo, and this session's answers. */
export function CommandPalette() {
  const { paletteOpen, setPaletteOpen, project, repo, messages, openCode, setActiveMessageId, status, activeConversationId } = useApp();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [issues, setIssues] = useState<IssueSummary[]>([]);
  const [loading, setLoading] = useState({ files: false, issues: false });
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const close = () => setPaletteOpen(false);

  useEffect(() => {
    if (paletteOpen) { setQuery(""); setHits([]); setIssues([]); setActive(0); requestAnimationFrame(() => input.current?.focus()); }
  }, [paletteOpen]);

  useEffect(() => {
    const q = query.trim();
    if (!paletteOpen || q.length < 2) { setHits([]); setIssues([]); return; }
    let cancelled = false;
    const timer = setTimeout(() => {
      if (project?.indexed) {
        setLoading((l) => ({ ...l, files: true }));
        api.projectSearch(project.path, q, 6).then((r) => !cancelled && setHits(r.hits)).catch(() => !cancelled && setHits([]))
          .finally(() => !cancelled && setLoading((l) => ({ ...l, files: false })));
      }
      if (repo && status?.github.read_repos.length) {
        setLoading((l) => ({ ...l, issues: true }));
        api.issues(repo, q).then((r) => !cancelled && setIssues(r.items.slice(0, 5))).catch(() => !cancelled && setIssues([]))
          .finally(() => !cancelled && setLoading((l) => ({ ...l, issues: false })));
      }
    }, 280);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [query, paletteOpen, project, repo, status]);

  const items = useMemo<Item[]>(() => {
    const q = query.trim().toLowerCase();
    const out: Item[] = [];
    const seen = new Set<string>();
    for (const h of hits) {
      const key = `${h.file_path}:${h.start_line}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ id: `f-${key}`, group: "Project", icon: <FileIcon path={h.file_path} size="sm" />, title: h.file_path,
        detail: [linesLabel(h.start_line, h.end_line), h.excerpt.split("\n").find((l) => l.trim())?.trim()].filter(Boolean).join(" · "),
        run: () => openCode(h.file_path, h.start_line, h.end_line) });
    }
    for (const i of issues) {
      out.push({ id: `i-${i.number}`, group: `GitHub · ${repo}`, icon: <CircleDot size={14} className={i.state === "open" ? "text-success" : "text-subtle"} />,
        title: i.title, detail: `#${i.number} · ${i.state}`, run: () => navigate("issues", { number: i.number, repo }) });
    }
    if (q) {
      for (const m of messages) {
        const text = `${m.question}\n${m.answer?.answer_markdown ?? ""}`.toLowerCase();
        if (text.includes(q)) {
          out.push({ id: `a-${m.id}`, group: "Answers in this session", icon: <Bot size={14} className="text-accent-text" />, title: m.question,
            detail: m.answer ? `${m.answer.project_sources.length} sources` : m.status, run: () => { setActiveMessageId(m.id); navigateChat(activeConversationId); } });
        }
      }
    }
    for (const n of NAV) {
      if (!q || n.label.toLowerCase().includes(q)) {
        out.push({ id: `p-${n.route}`, group: "Go to", icon: <span className="text-subtle [&_svg]:h-[14px] [&_svg]:w-[14px]">{n.icon}</span>, title: n.label, run: () => navigate(n.route) });
      }
    }
    return out;
  }, [hits, issues, messages, query, repo, openCode, setActiveMessageId, activeConversationId]);

  useEffect(() => { setActive(0); }, [items.length]);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!paletteOpen) return null;
  const choose = (item?: Item) => { if (!item) return; close(); item.run(); };
  const searching = loading.files || loading.issues;
  let lastGroup = "";

  return (
    <div className="fixed inset-0 z-[70] flex items-start justify-center bg-overlay px-4 pt-[12vh] backdrop-blur-[2px]" onMouseDown={close}>
      <div role="dialog" aria-modal="true" aria-label="Search" onMouseDown={(e) => e.stopPropagation()} data-testid="command-palette"
        className="animate-fade-up w-full max-w-[640px] overflow-hidden rounded-2xl border border-border bg-surface shadow-pop">
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Search size={17} className="text-subtle" />
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search files, issues, and answers…"
            aria-label="Search"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-results"
            aria-activedescendant={items[active] ? `palette-${items[active].id}` : undefined}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(items.length - 1, i + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
              else if (e.key === "Enter") { e.preventDefault(); choose(items[active]); }
              else if (e.key === "Escape") { e.preventDefault(); close(); }
            }}
            className="h-14 flex-1 bg-transparent text-[15px] text-fg outline-none placeholder:text-subtle"
          />
          {searching && <span className="dot-pulse h-1.5 w-1.5 rounded-full bg-accent" aria-label="Searching" />}
          <Kbd>Esc</Kbd>
        </div>
        <div ref={list} id="palette-results" role="listbox" className="max-h-[420px] overflow-y-auto p-2">
          {query.trim().length >= 2 && !searching && !hits.length && !issues.length && (
            <div className="px-3 py-2 text-[12.5px] text-muted">
              {project?.indexed ? "No matches in the project index" : "The project isn't indexed, so files can't be searched"}{repo ? " or GitHub." : "."}
            </div>
          )}
          {items.map((item, index) => {
            const header = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return (
              <div key={item.id}>
                {header && <div className="px-3 pb-1 pt-2.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-subtle">{header}</div>}
                <div
                  id={`palette-${item.id}`}
                  role="option"
                  aria-selected={index === active}
                  data-index={index}
                  data-testid="palette-item"
                  onMouseMove={() => setActive(index)}
                  onClick={() => choose(item)}
                  className={cx("flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2", index === active && "bg-accent-soft")}
                >
                  <span className="flex w-5 shrink-0 justify-center">{item.icon}</span>
                  <span className="min-w-0 flex-1">
                    <span className={cx("block truncate text-[13.5px]", item.group === "Project" ? "font-mono text-[12.5px]" : "", index === active ? "text-accent-text" : "text-fg")}>{item.title}</span>
                    {item.detail && <span className="block truncate text-[12px] text-muted">{item.detail}</span>}
                  </span>
                  {index === active && <CornerDownLeft size={13} className="shrink-0 text-accent-text" />}
                </div>
              </div>
            );
          })}
        </div>
        <div className="flex items-center gap-4 border-t border-border px-4 py-2 text-[11.5px] text-muted">
          <span className="flex items-center gap-1"><Kbd>↑</Kbd><Kbd>↓</Kbd> navigate</span>
          <span className="flex items-center gap-1"><Kbd>↵</Kbd> open</span>
          <span className="ml-auto flex items-center gap-1"><FileCode2 size={12} /> index · <CircleDot size={12} /> GitHub · <Bot size={12} /> answers</span>
        </div>
      </div>
    </div>
  );
}
