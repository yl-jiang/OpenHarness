import { useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { MarkdownView } from '../components/MarkdownView';
import { useApi } from '../hooks/useApi';

export function RecordDetail({ appName }: { appName: AppName }) {
  const { id = '' } = useParams();
  const { data, error, loading } = useApi(() => api.record(appName, id), [appName, id]);
  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Record not found.'}</div>;
  }
  return (
    <div className="page-stack">
      <div className="glass-card detail-card">
        <div className="card-header">
          <h2>{data.summary || data.date}</h2>
          <span className="chip">{data.emotion}</span>
        </div>
        <dl className="metadata-grid">
          <dt>Date</dt>
          <dd>{data.date}</dd>
          <dt>Tags</dt>
          <dd>{data.tags || '-'}</dd>
          <dt>Source</dt>
          <dd>{data.source}</dd>
          <dt>Entry</dt>
          <dd>{data.entry_id}</dd>
        </dl>
        <MarkdownView content={data.corrected_content || data.raw_content} />
      </div>
    </div>
  );
}
