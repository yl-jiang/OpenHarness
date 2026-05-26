import { useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { MarkdownView } from '../components/MarkdownView';
import { useApi } from '../hooks/useApi';

export function RecordDetail({ appName }: { appName: AppName }) {
  const { id = '' } = useParams();
  const { data, error, loading } = useApi(() => api.record(appName, id), [appName, id]);
  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Record not found.'}</div>;
  }
  return (
    <div className="max-w-3xl space-y-6">
      <div className="border border-border rounded-lg bg-surface-1 p-6">
        <div className="flex items-start justify-between mb-5">
          <h2 className="font-serif text-xl text-text m-0">{data.summary || data.date}</h2>
          <span className="text-[11px] px-2 py-0.5 rounded bg-accent-solo-dim text-accent-solo shrink-0 ml-4">{data.emotion}</span>
        </div>
        <div className="grid grid-cols-[100px_1fr] gap-x-4 gap-y-2 text-[13px] mb-6">
          <span className="text-text-muted">Date</span>
          <span className="text-text-secondary">{data.date}</span>
          <span className="text-text-muted">Tags</span>
          <span className="text-text-secondary">{data.tags || '—'}</span>
          <span className="text-text-muted">Source</span>
          <span className="text-text-secondary font-mono text-[12px]">{data.source}</span>
          <span className="text-text-muted">Entry</span>
          <span className="text-text-secondary font-mono text-[12px]">{data.entry_id}</span>
        </div>
        <div className="border-t border-border pt-5">
          <MarkdownView content={data.corrected_content || data.raw_content} />
        </div>
      </div>
    </div>
  );
}
