import { api } from '../api/client';
import type { AppName } from '../api/types';
import { DailyLineChart, EmotionPieChart, TagBarChart } from '../components/Charts';
import { useApi } from '../hooks/useApi';

export function Stats({ appName }: { appName: AppName }) {
  const { data, error, loading } = useApi(() => api.stats(appName), [appName]);
  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Failed to load stats.'}</div>;
  }
  return (
    <div className="dashboard-grid">
      <section className="glass-card">
        <h2>Daily records</h2>
        <DailyLineChart data={data.daily_counts} />
      </section>
      <section className="glass-card">
        <h2>Emotions</h2>
        <EmotionPieChart data={data.emotion_distribution} />
      </section>
      <section className="glass-card wide-card">
        <h2>Top tags</h2>
        <TagBarChart data={data.top_tags} />
      </section>
    </div>
  );
}
