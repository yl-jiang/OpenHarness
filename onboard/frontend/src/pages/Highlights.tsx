import { useState } from 'react';

import { api } from '../api/client';
import { DataTable } from '../components/DataTable';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

const kindColors: Record<string, string> = {
  blocker: 'text-danger',
  risk: 'text-warning',
  important: 'text-accent-solo',
  prompt: 'text-accent-wolo',
  tool: 'text-text-secondary',
};

const kindBadge: Record<string, string> = {
  blocker: 'border-danger/40 bg-danger/5 text-danger',
  risk: 'border-warning/40 bg-warning/5 text-warning',
  important: 'border-accent-solo/40 bg-accent-solo-dim text-accent-solo',
  prompt: 'border-accent-wolo/40 bg-accent-wolo-dim text-accent-wolo',
  tool: 'border-border bg-surface-2 text-text-secondary',
};

export function Highlights() {
  const { data, error, loading } = useApi(() => api.highlights(), [], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  const [kindFilter, setKindFilter] = useState<string>('all');

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text" role="alert">{error ?? 'Failed to load highlights.'}</div>;
  }

  const kinds = [...new Set(data.map((h) => h.kind))].sort();
  const filtered = kindFilter === 'all' ? data : data.filter((h) => h.kind === kindFilter);

  return (
    <div className="space-y-5">
      <div className="flex items-baseline justify-between flex-wrap gap-3">
        <h2 className="font-serif text-2xl text-text m-0">Highlights</h2>
        <div className="flex items-center gap-3">
          {kinds.length > 1 && (
            <select
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
              className="text-[12px] px-2 py-1 rounded-md border border-border bg-surface-2 text-text-secondary cursor-pointer focus:outline-none"
              aria-label="Filter by kind"
            >
              <option value="all">All kinds</option>
              {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          )}
          <span className="text-[11px] font-mono text-text-muted">{filtered.length} total</span>
        </div>
      </div>
      <DataTable
        rows={filtered}
        columns={[
          { key: 'kind', title: 'Kind', render: (row) => (
            <span className={`inline-block px-2 py-0.5 text-[11px] font-mono rounded border ${kindBadge[row.kind] ?? kindColors[row.kind] ?? ''}`}>
              {row.kind}
            </span>
          )},
          { key: 'title', title: 'Title', sortValue: (row) => row.title, render: (row) => <span className="font-medium text-text">{row.title}</span> },
          { key: 'project', title: 'Project', sortValue: (row) => row.project, render: (row) => <span className="font-mono text-[12px]">{row.project}</span> },
          { key: 'content', title: 'Content', render: (row) => <span className="line-clamp-2">{row.content}</span> },
        ]}
      />
    </div>
  );
}
