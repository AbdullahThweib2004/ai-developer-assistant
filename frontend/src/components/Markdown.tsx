import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { citationId, linkCitations } from "../lib/citations";
import { CitationChip } from "./CitationChip";

/** Markdown with [S#]/[G#] citations rendered as chips. Raw HTML is never rendered. */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-answer">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children: content }) {
            const id = citationId(href);
            if (id) return <CitationChip id={id} />;
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {content}
              </a>
            );
          },
        }}
      >
        {linkCitations(children)}
      </ReactMarkdown>
    </div>
  );
}
