/** "Lines 42–68" / "Line 7" for a source with a known range. */
export function linesLabel(start?: number | null, end?: number | null): string | null {
  if (start == null) return null;
  if (end == null || end === start) return `Line ${start}`;
  return `Lines ${start}–${end}`;
}

export function shortPath(path: string, max = 42): string {
  if (path.length <= max) return path;
  const parts = path.split("/");
  const name = parts.pop() ?? path;
  return parts.length ? `…/${name}` : name;
}
