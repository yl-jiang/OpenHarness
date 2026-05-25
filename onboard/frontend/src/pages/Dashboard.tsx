import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { ActivityHeatmap, EmotionPieChart } from '../components/Charts';
import { StatsCard } from '../components/StatsCard';
import { useApi } from '../hooks/useApi';

export function Dashboard({ appName }: { appName: AppName }) {
  const { data, error, loading } = useApi(() => api.stats(appName), [appName]);

  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Failed to load dashboard.'}</div>;
  }

  return (
    <div className="page-stack">
      <div className="stats-grid">
        <StatsCard label="Entries" value={data.total_entries} />
        <StatsCard label="Records" value={data.total_records} />
        <StatsCard label="This week" value={data.this_week_records} />
        <StatsCard label="Pending todos" value={data.pending_todos} />
      </div>
      <div className="dashboard-grid">
        <section className="glass-card">
          <div className="card-header">
            <h2>Activity</h2>
            <Link to="/records">View records</Link>
          </div>
          <ActivityHeatmap data={data.daily_counts} />
        </section>
        <section className="glass-card">
          <div className="card-header">
            <h2>Emotion distribution</h2>
          </div>
          <EmotionPieChart data={data.emotion_distribution} />
        </section>
      </div>
      {appName === 'wolo' ? (
        <div className="stats-grid">
          <StatsCard label="Decisions" value={data.total_decisions ?? 0} />
          <StatsCard label="Highlights" value={data.total_highlights ?? 0} />
          <StatsCard label="Open blockers" value={data.open_blockers ?? 0} />
        </div>
      ) : null}
    </div>
  );
}
