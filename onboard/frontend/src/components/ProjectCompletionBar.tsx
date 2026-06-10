export function ProjectCompletionBar({ pct }: { pct: number | null }) {
  if (pct === null) {
    return (
      <div className="flex items-center gap-2 text-xs text-text-muted">
        <div className="h-1.5 w-20 rounded-full bg-surface-3" />
        <span>Not quantified</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="h-1.5 w-20 rounded-full bg-surface-3 overflow-hidden">
        <div
          className="h-full rounded-full bg-accent-solo transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-text-secondary">{pct}%</span>
    </div>
  );
}
