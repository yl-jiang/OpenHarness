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

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" />
      {labels[status] ?? status}
    </span>
  );
}
