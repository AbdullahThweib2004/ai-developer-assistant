import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import yaml from "highlight.js/lib/languages/yaml";
import markdown from "highlight.js/lib/languages/markdown";
import bash from "highlight.js/lib/languages/bash";
import ini from "highlight.js/lib/languages/ini";

// Only the languages we index: keeps the bundle small (core + 8 grammars).
hljs.registerLanguage("python", python);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("ini", ini);

/** Highlighted HTML per line (escaped by highlight.js). Falls back to escaped plain text. */
export function highlightLines(code: string, language?: string | null): string[] {
  let html: string;
  if (language && hljs.getLanguage(language)) {
    html = hljs.highlight(code, { language, ignoreIllegals: true }).value;
  } else {
    html = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  // Re-open spans that cross line breaks so each line renders on its own.
  const lines: string[] = [];
  let open: string[] = [];
  for (const raw of html.split("\n")) {
    const line = open.join("") + raw;
    const tags = raw.match(/<span[^>]*>|<\/span>/g) ?? [];
    for (const tag of tags) {
      if (tag.startsWith("</")) open.pop();
      else open.push(tag);
    }
    lines.push(line + "</span>".repeat(open.length));
  }
  return lines;
}
