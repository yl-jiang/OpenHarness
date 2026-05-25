interface StatsCardProps {
  label: string;
  value: number | string;
  hint?: string;
}

export function StatsCard({ label, value, hint }: StatsCardProps) {
  return (
    <article className="glass-card stat-card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </article>
  );
}
