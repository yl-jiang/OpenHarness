import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { ActivityHeatmap, EmotionPieChart } from '../components/Charts';
import { StatsCard } from '../components/StatsCard';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

export function Dashboard({ appName }: { appName: AppName }) {
  const { data, error, loading } = useApi(() => api.stats(appName), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Failed to load dashboard.'}</div>;
  }

  return (
    <div className="space-y-6">
      {/* Section label */}
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-2xl text-text m-0">Overview</h2>
        <span className="text-[11px] font-mono text-text-muted uppercase tracking-wider">{appName} · today</span>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-5 gap-3">
        <StatsCard label="Entries" value={data.total_entries} />
        <StatsCard label="Records" value={data.total_records} />
        <StatsCard label="This week" value={data.this_week_records} />
        <StatsCard label="Pending todos" value={data.pending_todos} />
        <StatsCard label="LLM calls" value={data.llm_total_calls} />
      </div>

      <section className="p-5 border border-border rounded-lg bg-surface-1">
        <div className="flex items-center justify-between gap-4 mb-4">
          <h3 className="text-sm font-medium text-text m-0">LLM Model Usage</h3>
          <span className="text-[12px] font-mono text-text-muted">
            total {data.llm_total_calls.toLocaleString()}
          </span>
        </div>
        {data.llm_usage_models.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {data.llm_usage_models.map((item) => (
              <div
                key={item.model}
                className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-[12px]"
              >
                <span className="font-mono text-text">{item.model}</span>
                <span className="text-text-muted">{item.count.toLocaleString()} calls</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-text-muted">No LLM calls yet.</div>
        )}
      </section>

      {/* Charts grid */}
      <div className="grid grid-cols-2 gap-4">
        <section className="p-5 border border-border rounded-lg bg-surface-1">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-text m-0">Activity</h3>
            <Link to="/records" className="text-[12px] text-text-muted hover:text-text no-underline transition-colors">View all →</Link>
          </div>
          <ActivityHeatmap data={data.daily_counts} />
        </section>
        <section className="p-5 border border-border rounded-lg bg-surface-1">
          <h3 className="text-sm font-medium text-text m-0 mb-4">Emotion Distribution</h3>
          <EmotionPieChart data={data.emotion_distribution} />
        </section>
      </div>

      {/* Wolo extras */}
      {appName === 'wolo' ? (
        <div className="grid grid-cols-3 gap-3">
          <StatsCard label="Decisions" value={data.total_decisions ?? 0} />
          <StatsCard label="Highlights" value={data.total_highlights ?? 0} />
          <StatsCard label="Open blockers" value={data.open_blockers ?? 0} />
        </div>
      ) : null}
    </div>
  );
}
