import { Check, MessageSquare, Pencil, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { useApp } from "../lib/store";
import type { Conversation } from "../lib/types";
import { IconButton, cx } from "./ui";

export function relativeTime(epochSeconds: number): string {
  const seconds = Date.now() / 1000 - epochSeconds;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

function Item({ conversation, active }: { conversation: Conversation; active: boolean }) {
  const { openConversation, renameConversation, deleteConversation } = useApp();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [title, setTitle] = useState(conversation.title);
  const label = conversation.title || "New conversation";

  if (editing) {
    return (
      <form className="flex items-center gap-1 rounded-lg bg-surface px-1.5 py-1"
        onSubmit={(e) => { e.preventDefault(); if (title.trim()) void renameConversation(conversation.id, title.trim()); setEditing(false); }}>
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Conversation title" maxLength={200}
          onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
          className="h-7 min-w-0 flex-1 rounded-md border border-accent bg-surface px-2 text-[12.5px] text-fg outline-none" />
        <IconButton label="Save title" type="submit"><Check size={13} /></IconButton>
        <IconButton label="Cancel rename" onClick={() => setEditing(false)}><X size={13} /></IconButton>
      </form>
    );
  }
  if (confirming) {
    return (
      <div className="flex items-center gap-1.5 rounded-lg bg-danger-soft px-2.5 py-1.5 text-[12.5px]" role="alertdialog" aria-label="Delete conversation">
        <span className="min-w-0 flex-1 truncate text-fg">Delete "{label}"?</span>
        <button type="button" onClick={() => { setConfirming(false); void deleteConversation(conversation.id); }}
          className="rounded-md px-1.5 py-0.5 font-medium text-danger hover:bg-danger/10">Delete</button>
        <button type="button" onClick={() => setConfirming(false)} className="rounded-md px-1.5 py-0.5 text-muted hover:bg-surface-2">Cancel</button>
      </div>
    );
  }
  return (
    <div className={cx("group flex items-center rounded-lg transition-colors", active ? "bg-surface shadow-soft" : "hover:bg-surface-2")}
      data-testid="conversation-item" data-active={active || undefined}>
      <button type="button" onClick={() => void openConversation(conversation.id)} aria-current={active ? "true" : undefined}
        className="min-w-0 flex-1 px-2.5 py-1.5 text-left" title={label}>
        <span className={cx("block truncate text-[12.5px]", active ? "font-medium text-fg" : "text-muted")}>{label}</span>
        <span className="block text-[11px] text-subtle">{relativeTime(conversation.updated)}{conversation.saved === false ? " · not saved" : ""}</span>
      </button>
      <span className="flex shrink-0 pr-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <IconButton label={`Rename ${label}`} onClick={() => { setTitle(conversation.title); setEditing(true); }}><Pencil size={12} /></IconButton>
        <IconButton label={`Delete ${label}`} onClick={() => setConfirming(true)}><Trash2 size={12} /></IconButton>
      </span>
    </div>
  );
}

/** Conversations of the CURRENT project only. */
export function ConversationList() {
  const { conversations, activeConversationId, newChat, project } = useApp();
  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="Conversations" data-testid="conversation-list">
      <div className="flex items-center justify-between px-1 pb-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-subtle">Conversations</span>
        <button type="button" onClick={newChat} disabled={!project} data-testid="new-chat"
          className="flex h-6 items-center gap-1 rounded-md px-1.5 text-[12px] font-medium text-accent-text transition-colors hover:bg-accent-soft disabled:opacity-50">
          <Plus size={13} /> New chat
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
        {conversations.length === 0 ? (
          <p className="flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] text-subtle"><MessageSquare size={12} /> No conversations in {project?.name ?? "this project"} yet.</p>
        ) : conversations.map((c) => <Item key={c.id} conversation={c} active={c.id === activeConversationId} />)}
      </div>
    </section>
  );
}
