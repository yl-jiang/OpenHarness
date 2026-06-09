import { useState } from 'react';

import { api } from '../api/client';
import { DataTable } from '../components/DataTable';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

export function Decisions() {
  const { data, error, loading } = useApi(() => api.decisions(), [], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  const [projectFilter, setProjectFilter] = useState<string>('all');

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text" role="alert">{error ?? 'Failed to load decisions.'}</div>;
  }

  const projects = [...new Set(data.map((d) => d.project))].sort();
  const filtered = projectFilter === 'all' ? data : data.filter((d) => d.project === projectFilter);

  return (
    <div className="space-y-5">
      <div className="flex items-baseline justify-between flex-wrap gap-3">
        <h2 className="font-serif text-2xl text-text m-0">Decisions</h2>
        <div className="flex items-center gap-3">
          {projects.length > 1 && (
            <select
              value={projectFilter}
              onChange={(e) => setProjectFilter(e.target.value)}
              className="text-[12px] px-2 py-1 rounded-md border border-border bg-surface-2 text-text-secondary cursor-pointer focus:outline-none"
              aria-label="Filter by project"
            >
              <option value="all">All projects</option>
              {projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          )}
          <span className="text-[11px] font-mono text-text-muted">{filtered.length} total</span>
        </div>
      </div>
      <DataTable
        rows={filtered}
        columns={[
          { key: 'title', title: 'Title', sortValue: (row) => row.title, render: (row) => <span className="font-medium text-text">{row.title}</span> },
          { key: 'project', title: 'Project', sortValue: (row) => row.project, render: (row) => <span className="font-mono text-[12px]">{row.project}</span> },
          { key: 'impact', title: 'Impact', render: (row) => {
            const impact = row.impact?.toLowerCase() ?? '';
            const color = impact.includes('high') || impact.includes('critical')
              ? 'border-danger/40 bg-danger/5'
              : impact.includes('medium')
              ? 'border-warning/40 bg-warning/5'
              : 'border-border bg-surface-2';
            return (
              <span className={`inline-block px-2 py-0.5 text-[12px] rounded border ${color} line-clamp-1`}>
                {row.impact || '—'}
              </span>
            );
          }},
          { key: 'rationale', title: 'Rationale', render: (row) => <span className="line-clamp-2">{row.rationale}</span> },
        ]}
      />
    </div>
  );
}
