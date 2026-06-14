import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName, LogRecord } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

function tags(text: string) {
  return text
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean);
}

export function Records({ appName }: { appName: AppName }) {
  const { data, error, loading, reload } = useApi(() => api.records(appName, { limit: 100 }), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  const [pendingDelete, setPendingDelete] = useState<LogRecord | null>(null);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await api.deleteRecord(appName, pendingDelete.id);
      reload();
    } catch { /* ignore */ }
    setDeleting(false);
    setPendingDelete(null);
  };

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Failed to load records.'}</div>;
  }
  return (
    <div className="space-y-5">
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-2xl text-text m-0">Records</h2>
        <span className="text-[11px] font-mono text-text-muted">{data.total} total</span>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">
        {[...data.items].sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime()).map((record, index) => (
          <article
            key={record.id}
            className="group flex flex-col h-[200px] p-4 border border-border rounded-lg bg-surface-1 hover:bg-surface-2 hover:border-text-muted/30 transition-all animate-[fade-in_0.3s_ease-out_both]"
            style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
          >
            {/* Top: date + emotion tag — fixed at top */}
            <div className="flex items-center justify-between mb-2 shrink-0">
              <span className="font-mono text-[11px] text-text-muted">{record.date}</span>
              <span className="text-[11px] px-1.5 py-0.5 rounded bg-accent-solo-dim text-accent-solo">{record.emotion || 'neutral'}</span>
            </div>
            {/* Middle: title + content — flex-1 fills remaining space, overflow hidden */}
            <div className="flex-1 min-h-0 overflow-hidden mb-3">
              <h3 className="text-sm font-medium text-text m-0 mb-1.5 line-clamp-1">
                {record.summary || record.corrected_content || record.raw_content}
              </h3>
              <p className="text-[13px] text-text-secondary m-0 line-clamp-3">
                {record.corrected_content || record.raw_content}
              </p>
            </div>
            {/* Bottom: tags + actions — fixed at bottom */}
            <div className="flex items-center justify-between shrink-0">
              <div className="flex flex-wrap gap-1">
                {tags(record.tags).slice(0, 3).map((tag) => (
                  <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-surface-3 text-text-muted">{tag}</span>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setPendingDelete(record)}
                  className="opacity-0 group-hover:opacity-100 transition-opacity p-1 text-text-muted hover:text-danger rounded"
                  title="Delete record"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                </button>
                <Link className="text-[12px] text-text-muted hover:text-accent-solo no-underline transition-colors" to={`/records/${record.id}`}>
                  →
                </Link>
              </div>
            </div>
          </article>
        ))}
      </div>
      {pendingDelete && (
        <ConfirmDialog
          open
          title="Delete Record"
          description="This will permanently delete this record and all its associated todos, experiments, and other derived data. This action cannot be undone."
          confirmLabel="Delete"
          danger
          onConfirm={handleDelete}
          onCancel={() => setPendingDelete(null)}
          loading={deleting}
        />
      )}
    </div>
  );
}
