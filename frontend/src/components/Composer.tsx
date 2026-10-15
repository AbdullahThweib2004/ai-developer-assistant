import { Boxes, ChevronDown, FolderSearch, Send, Square, Zap } from "lucide-react";
import { forwardRef, type ReactNode, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { ChatMode } from "../lib/store";
import { GithubMark } from "./GithubMark";
import { Select } from "./Select";
import { cx } from "./ui";

export interface ComposerHandle { focus: () => void; setText: (t: string) => void }

const MODES: { value: ChatMode; label: string; description: string; icon: ReactNode }[] = [
  { value: "auto", label: "Auto", description: "Decides per question: project, GitHub, both or neither", icon: <Boxes size={14} /> },
  { value: "project", label: "Project", description: "Only searches and reads the local project", icon: <FolderSearch size={14} /> },
  { value: "github", label: "GitHub", description: "Only searches the selected GitHub repository", icon: <GithubMark size={13} /> },
  { value: "quick", label: "Quick answer", description: "One retrieval + one LLM call, no tools", icon: <Zap size={14} /> },
];

export const Composer = forwardRef<ComposerHandle, {
  running: boolean; disabled?: boolean; disabledReason?: string; mode: ChatMode; onMode: (m: ChatMode) => void;
  onSend: (text: string) => void; onStop: () => void; githubAvailable: boolean;
}>(function Composer({ running, disabled, disabledReason, mode, onMode, onSend, onStop, githubAvailable }, ref) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => area.current?.focus(), setText: (t) => { setText(t); area.current?.focus(); } }));

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  const send = () => {
    const value = text.trim();
    if (!value || running || disabled) return;
    onSend(value);
    setText("");
  };
  const current = MODES.find((m) => m.value === mode) ?? MODES[0];

  return (
    <div className={cx(
      "rounded-2xl border bg-surface shadow-card transition-[border-color,box-shadow] duration-150",
      disabled ? "border-border opacity-80" : "border-border focus-within:border-accent focus-within:shadow-[0_0_0_3px_var(--ring)]",
    )}>
      <textarea
        ref={area}
        value={text}
        rows={1}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
        placeholder={disabled ? disabledReason : "Ask a follow-up, explain a function, or investigate another issue…"}
        aria-label="Message"
        className="block max-h-[200px] w-full resize-none bg-transparent px-4 pb-2 pt-3.5 text-[14px] leading-6 text-fg outline-none placeholder:text-subtle"
      />
      <div className="flex items-center justify-between gap-2 px-3 pb-3">
        <Select
          label="Answer mode"
          testId="mode-select"
          value={mode}
          onChange={onMode}
          placement="top"
          menuClassName="w-[300px]"
          options={MODES.map((m) => ({ ...m, disabled: m.value === "github" && !githubAvailable }))}
          trigger={(_, open) => (
            <span className="flex h-7 items-center gap-1.5 rounded-md border border-border bg-surface-2 px-2 text-[12px] font-medium text-muted transition-colors hover:text-fg">
              {current.icon}
              {current.label}
              <ChevronDown size={12} className={cx("transition-transform", open && "rotate-180")} />
            </span>
          )}
        />
        <div className="flex items-center gap-2">
          <span className="hidden text-[11.5px] text-subtle sm:inline">Enter to send · Shift+Enter for a new line</span>
          {running ? (
            <button type="button" onClick={onStop} aria-label="Stop"
              className="flex h-9 w-9 items-center justify-center rounded-full bg-fg text-bg transition-opacity hover:opacity-85">
              <Square size={12} fill="currentColor" />
            </button>
          ) : (
            <button type="button" onClick={send} aria-label="Send" disabled={!text.trim() || disabled}
              className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-accent-fg shadow-soft transition-[background-color,opacity] hover:bg-accent-hover disabled:opacity-40">
              <Send size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
});
