import { Link, useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { SearchBar } from '../components/SearchBar';
import { useApi } from '../hooks/useApi';

export function Search({ appName }: { appName: AppName }) {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const { data, error, loading } = useApi(() => api.search(appName, { q, limit: 50 }), [appName, q]);
  return (
    <div className="page-stack">
      <SearchBar initialValue={q} onSearch={(value) => setParams(value ? { q: value } : {})} />
      {loading ? <div className="skeleton-grid" /> : null}
      {error ? <div className="error-state">{error}</div> : null}
      <div className="record-grid">
        {(data?.records ?? []).map((record) => (
          <article key={record.id} className="record-card glass-card">
            <h3>{record.summary}</h3>
            <p>{record.corrected_content}</p>
            <Link className="card-link" to={`/records/${record.id}`}>
              Details
            </Link>
          </article>
        ))}
      </div>
    </div>
  );
}
