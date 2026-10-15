import { ArrowUp, Square, Zap } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Kbd, cx } from "./ui";

export interface ComposerHandle { focus: () => void; setText: (t: string) => void }

export const Composer = forwardRef<ComposerHandle, {
  running: boolean; disabled?: boolean; disabledReason?: string; quick: boolean; onQuick: (v: boolean) => void;
  onSend: (text: string) => void; onStop: () => void;
}>(function Composer({ running, disabled, disabledReason, quick, onQuick, onSend, onStop }, ref) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => area.current?.focus(), setText: (t) => setText(t) }));

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

  return (
    <div className="mx-auto w-full max-w-[820px] px-6 pb-5">
      <div className={cx(
        "rounded-2xl border bg-surface shadow-pop transition-colors duration-150",
        disabled ? "border-border opacity-80" : "border-border focus-within:border-border-strong",
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
          placeholder={disabled ? disabledReason : quick ? "Ask a quick question about the code…" : "Ask about the code, an error, or a GitHub issue…"}
          aria-label="Message"
          className="block max-h-[200px] w-full resize-none bg-transparent px-4 pb-2 pt-3.5 text-[14px] leading-6 text-fg outline-none placeholder:text-subtle"
        />
        <div className="flex items-center justify-between px-2.5 pb-2.5">
          <button
            type="button"
            onClick={() => onQuick(!quick)}
            aria-pressed={quick}
            title="Quick answer: one retrieval and one LLM call, no tools"
            className={cx(
              "flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] font-medium transition-colors duration-150",
              quick ? "bg-accent-soft text-accent-text" : "text-subtle hover:bg-surface-2 hover:text-fg",
            )}
          >
            <Zap size={13} /> Quick answer
          </button>
          <div className="flex items-center gap-2">
            <span className="hidden items-center gap-1 text-[11px] text-subtle sm:flex"><Kbd>⏎</Kbd> send <Kbd>⇧⏎</Kbd> newline</span>
            {running ? (
              <button type="button" onClick={onStop} aria-label="Stop"
                className="flex h-8 w-8 items-center justify-center rounded-lg bg-fg text-bg transition-opacity hover:opacity-85">
                <Square size={12} fill="currentColor" />
              </button>
            ) : (
              <button type="button" onClick={send} aria-label="Send" disabled={!text.trim() || disabled}
                className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-accent-fg transition-[background-color,opacity] hover:bg-accent-hover disabled:opacity-35">
                <ArrowUp size={16} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});
