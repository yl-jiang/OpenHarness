import { useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { MarkdownView } from '../components/MarkdownView';
import { useApi } from '../hooks/useApi';

export function ReportView({ appName }: { appName: AppName }) {
  const { id = '' } = useParams();
  const { data, error, loading } = useApi(() => api.report(appName, id), [appName, id]);
  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Report not found.'}</div>;
  }
  return (
    <article className="glass-card detail-card">
      <div className="card-header">
        <h2>{data.report_type} report</h2>
        <span>{data.created_at}</span>
      </div>
      <MarkdownView content={data.content} />
    </article>
  );
}
