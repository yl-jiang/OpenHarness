import { useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { MarkdownView } from '../components/MarkdownView';
import { useApi } from '../hooks/useApi';

function formatCreatedAt(raw: string): string {
  const d = new Date(raw);
  if (isNaN(d.getTime())) return raw;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

export function ReportView({ appName }: { appName: AppName }) {
  const { id = '' } = useParams();
  const { data, error, loading } = useApi(() => api.report(appName, id), [appName, id]);
  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Report not found.'}</div>;
  }
  return (
    <article className="max-w-3xl border border-border rounded-lg bg-surface-1 p-6">
      <div className="flex items-center justify-between mb-5 pb-4 border-b border-border">
        <h2 className="font-serif text-xl text-text m-0 capitalize">{data.report_type} Report</h2>
        <span className="text-[11px] font-mono text-text-muted">{formatCreatedAt(data.created_at)}</span>
      </div>
      {data.content ? (
        <MarkdownView content={data.content} />
      ) : (
        <div className="border border-warning/30 rounded-md bg-warning/5 p-5 space-y-2">
          <p className="text-sm text-text m-0 font-medium">Report generated but no content was produced.</p>
          <p className="text-[13px] text-text-secondary m-0 leading-relaxed">
            Possible causes: AI provider not configured (check Settings → Gateway), no records in the selected period, or the LLM returned an empty response.
          </p>
        </div>
      )}
    </article>
  );
}
