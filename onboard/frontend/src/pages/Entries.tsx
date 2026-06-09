import { api } from '../api/client';
import type { AppName, Entry } from '../api/types';
import { DataTable } from '../components/DataTable';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

function formatTime(raw: string): { date: string; time: string } {
  const d = new Date(raw);
  if (isNaN(d.getTime())) {
    // fallback: try to extract date/time from ISO-ish string
    const [datePart, timePart] = raw.split(/[T ]/);
    return { date: datePart ?? raw, time: timePart?.slice(0, 5) ?? '' };
  }
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / 86400000);

  let date: string;
  if (diffDays === 0) date = 'Today';
  else if (diffDays === 1) date = 'Yesterday';
  else if (diffDays < 7) date = d.toLocaleDateString(undefined, { weekday: 'short' });
  else date = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
  return { date, time };
}

export function Entries({ appName }: { appName: AppName }) {
  const { data, error, loading } = useApi(() => api.entries(appName, { limit: 100 }), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Failed to load entries.'}</div>;
  }
  return (
    <div className="space-y-5">
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-2xl text-text m-0">Entries</h2>
        <span className="text-[11px] font-mono text-text-muted">{data.total} total</span>
      </div>
      <DataTable<Entry>
        rows={[...data.items].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())}
        pageSize={30}
        columns={[
          { key: 'created_at', title: 'Time', sortValue: (row) => new Date(row.created_at).getTime(), render: (row) => {
            const { date, time } = formatTime(row.created_at);
            return (
              <span className="font-mono text-[12px] whitespace-nowrap">
                <span className="text-text-secondary">{date}</span>
                <span className="text-text-muted ml-1.5">{time}</span>
              </span>
            );
          }},
          { key: 'channel', title: 'Channel', sortValue: (row) => row.channel, render: (row) => <span className="inline-block px-1.5 py-0.5 text-[11px] rounded bg-surface-3 text-text-secondary">{row.channel}</span> },
          { key: 'content', title: 'Content', render: (row) => <span className="text-text line-clamp-2">{row.content}</span> },
        ]}
      />
    </div>
  );
}
