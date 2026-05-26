interface StatusBadgeProps {
  status: string;
}

const labels: Record<string, string> = {
  pending: 'pending',
  in_progress: 'in progress',
  done: 'done',
  running: 'running',
  stopped: 'stopped',
  unknown: 'unknown',
};

const dotColor: Record<string, string> = {
  running: 'bg-success',
  done: 'bg-success',
  in_progress: 'bg-warning',
  stopped: 'bg-danger',
  pending: 'bg-text-muted',
  unknown: 'bg-text-muted',
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const animate = status === 'running' || status === 'in_progress';
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-mono text-text-muted">
      <span
        className={`w-1.5 h-1.5 rounded-full ${dotColor[status] ?? 'bg-text-muted'} ${animate ? 'animate-[pulse-dot_1.4s_ease-in-out_infinite]' : ''}`}
      />
      {labels[status] ?? status}
    </span>
  );
}
