import { useMemo } from "react";
import { highlightLines } from "../lib/highlight";
import { CopyButton } from "./ui";

/** Syntax-highlighted code with line numbers. ``code`` is untrusted project text (rendered escaped). */
export function CodeBlock({ code, language, startLine = 1, maxHeight = 320 }: {
  code: string; language?: string | null; startLine?: number | null; maxHeight?: number;
}) {
  const lines = useMemo(() => highlightLines(code.replace(/\n+$/, ""), language), [code, language]);
  const first = startLine ?? 1;
  const width = String(first + lines.length).length;
  return (
    <div className="group relative overflow-hidden rounded-lg border border-border bg-code">
      <CopyButton text={code} label="Copy code" className="absolute right-1.5 top-1.5 z-10 bg-surface/80 opacity-0 backdrop-blur group-hover:opacity-100 focus:opacity-100" />
      <div className="overflow-auto py-2" style={{ maxHeight }}>
        <table className="w-full border-collapse font-mono text-[11.5px] leading-[1.6]">
          <tbody>
            {lines.map((html, i) => (
              <tr key={i}>
                <td
                  className="select-none pl-3 pr-3 text-right align-top text-subtle/70"
                  style={{ width: `${width + 2}ch` }}
                  aria-hidden
                >
                  {first + i}
                </td>
                <td className="whitespace-pre pr-4 text-fg" dangerouslySetInnerHTML={{ __html: html || " " }} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
