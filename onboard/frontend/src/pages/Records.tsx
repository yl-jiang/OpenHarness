import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { useApi } from '../hooks/useApi';

function tags(text: string) {
  return text
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean);
}

export function Records({ appName }: { appName: AppName }) {
  const { data, error, loading } = useApi(() => api.records(appName, { limit: 100 }), [appName]);
  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Failed to load records.'}</div>;
  }
  return (
    <div className="page-stack">
      <h2>Records</h2>
      <div className="record-grid">
        {data.items.map((record) => (
          <article key={record.id} className="record-card glass-card">
            <div className="record-date">{record.date}</div>
            <h3>{record.summary || record.corrected_content || record.raw_content}</h3>
            <p>{record.corrected_content || record.raw_content}</p>
            <div className="chip-row">
              <span className="chip">{record.emotion || 'neutral'}</span>
              {tags(record.tags).map((tag) => (
                <span key={tag} className="chip">
                  {tag}
                </span>
              ))}
            </div>
            <Link className="card-link" to={`/records/${record.id}`}>
              Details
            </Link>
          </article>
        ))}
      </div>
    </div>
  );
}
