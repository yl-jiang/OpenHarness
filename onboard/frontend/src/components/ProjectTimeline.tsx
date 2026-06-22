import type { TimelineEvent } from "../api/types";

const TYPE_BADGE: Record<string, { label: string; color: string }> = {
  milestone: { label: "Milestone", color: "var(--color-accent-solo)" },
  milestone_completed: { label: "Completed", color: "var(--color-success)" },
  milestone_target: { label: "Target", color: "var(--color-text-muted)" },
  project_created: { label: "Created", color: "var(--color-info)" },
  project_completed: { label: "Done", color: "var(--color-success)" },
  snapshot: { label: "Snapshot", color: "var(--color-text-muted)" },
  signal_progress: { label: "Progress", color: "var(--color-success)" },
  signal_blocker: { label: "Blocker", color: "var(--color-danger)" },
  signal_risk: { label: "Risk", color: "var(--color-warning)" },
  signal_stale: { label: "Stale", color: "var(--color-warning)" },
  signal_momentum: { label: "Momentum", color: "var(--color-success)" },
  signal_decision: { label: "Decision", color: "var(--color-info)" },
  record: { label: "Record", color: "var(--color-info)" },
  signal_milestone_evidence: { label: "Milestone", color: "var(--color-success)" },
};

function badge(event: TimelineEvent) {
  const b = TYPE_BADGE[event.type] ?? { label: event.type, color: "var(--color-text-muted)" };
  return b;
}

function formatDate(iso: string): string {
  if (!iso) return "";
  const d = iso.length > 10 ? iso.slice(0, 10) : iso;
  return d;
}

export function ProjectTimeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="text-[12px] text-text-muted py-3">
        No timeline events yet. Analyze the project to generate signals and snapshots.
      </div>
    );
  }

  const SCROLL_THRESHOLD = 8;
  const needsScroll = events.length > SCROLL_THRESHOLD;

  return (
    <div className="relative">
      <div className={`relative pl-6 ${needsScroll ? 'max-h-[340px] overflow-y-auto pr-2' : ''}`}>
        {/* Vertical line */}
        <div className="absolute left-[9px] top-1 bottom-1 w-px bg-border" />

      {events.map((ev, i) => {
        const b = badge(ev);
        return (
          <div key={i} className="relative pb-3 last:pb-0">
            {/* Dot */}
            <div
              className="absolute left-[-15px] top-[5px] w-[9px] h-[9px] rounded-full border-2 border-surface-1"
              style={{ backgroundColor: b.color }}
            />

            {/* Content */}
            <div className="min-w-0">
              <div className="flex items-center gap-2 mb-0.5">
                <span
                  className="text-[10px] font-medium px-1.5 py-0.5 rounded-sm shrink-0"
                  style={{ color: b.color, backgroundColor: `color-mix(in srgb, ${b.color} 12%, transparent)` }}
                >
                  {b.label}
                </span>
                <span className="text-[10px] text-text-muted tabular-nums shrink-0">
                  {formatDate(ev.date)}
                </span>
              </div>
              <div className="text-[12px] text-text leading-snug truncate">
                {ev.title}
              </div>
              {ev.detail && (
                <div className="text-[11px] text-text-muted truncate">
                  {ev.detail}
                </div>
              )}
            </div>
          </div>
        );
      })}
      </div>
      {needsScroll && (
        <div className="absolute bottom-0 left-0 right-0 h-8 bg-gradient-to-t from-surface-1 to-transparent pointer-events-none" />
      )}
    </div>
  );
}
