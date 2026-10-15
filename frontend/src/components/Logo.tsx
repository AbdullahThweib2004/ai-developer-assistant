export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="shrink-0">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M11 11l-4 5 4 5M21 11l4 5-4 5M17.5 9.5l-3 13" stroke="var(--accent-fg)" strokeWidth="2.4"
        fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
