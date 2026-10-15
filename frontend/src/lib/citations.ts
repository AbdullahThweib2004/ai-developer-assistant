const GROUP = /\[\s*([SG]\d+(?:\s*[,;]\s*[SG]\d+)*)\s*\]/g;

/** Turn [S1] / [G2] / [S1, G2] into markdown links the renderer turns into citation chips. */
export function linkCitations(markdown: string): string {
  return markdown.replace(GROUP, (_, group: string) =>
    group.split(/\s*[,;]\s*/).map((id) => `[${id}](#cite-${id})`).join(""),
  );
}

export function citationId(href?: string): string | null {
  return href?.startsWith("#cite-") ? href.slice(6) : null;
}
