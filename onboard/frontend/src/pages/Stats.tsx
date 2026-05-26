import { api } from '../api/client';
import type { AppName } from '../api/types';
import { DailyLineChart, EmotionPieChart, TagBarChart } from '../components/Charts';
import { useApi } from '../hooks/useApi';

export function Stats({ appName }: { appName: AppName }) {
  const { data, error, loading } = useApi(() => api.stats(appName), [appName]);
  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Failed to load stats.'}</div>;
  }
  return (
    <div className="space-y-5">
      <h2 className="font-serif text-2xl text-text m-0">Statistics</h2>
      <div className="grid grid-cols-2 gap-4">
        <section className="p-5 border border-border rounded-lg bg-surface-1">
          <h3 className="text-sm font-medium text-text m-0 mb-4">Daily Records</h3>
          <DailyLineChart data={data.daily_counts} />
        </section>
        <section className="p-5 border border-border rounded-lg bg-surface-1">
          <h3 className="text-sm font-medium text-text m-0 mb-4">Emotions</h3>
          <EmotionPieChart data={data.emotion_distribution} />
        </section>
        <section className="p-5 border border-border rounded-lg bg-surface-1 col-span-2">
          <h3 className="text-sm font-medium text-text m-0 mb-4">Top Tags</h3>
          <TagBarChart data={data.top_tags} />
        </section>
      </div>
    </div>
  );
}
