import { Link, useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { SearchBar } from '../components/SearchBar';
import { useApi } from '../hooks/useApi';

export function Search({ appName }: { appName: AppName }) {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const { data, error, loading } = useApi(() => api.search(appName, { q, limit: 50 }), [appName, q]);
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-4">
        <h2 className="font-serif text-2xl text-text m-0">Search</h2>
        <SearchBar initialValue={q} onSearch={(value) => setParams(value ? { q: value } : {})} />
      </div>
      {loading ? <div className="h-40 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" /> : null}
      {error ? <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error}</div> : null}
      {data && data.records.length === 0 && q ? (
        <EmptyState icon={<span>⌕</span>} title={`No results for "${q}"`} description="Try different keywords or check your spelling." />
      ) : null}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">
        {[...(data?.records ?? [])].sort((a, b) => (b.date || b.created_at).localeCompare(a.date || a.created_at)).map((record, index) => (
          <article
            key={record.id}
            className="p-4 border border-border rounded-lg bg-surface-1 hover:bg-surface-2 hover:border-text-muted/30 transition-all animate-[fade-in_0.3s_ease-out_both]"
            style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
          >
            <h3 className="text-sm font-medium text-text m-0 mb-1 line-clamp-1">{record.summary}</h3>
            <p className="text-[13px] text-text-secondary m-0 line-clamp-2 mb-2">{record.corrected_content}</p>
            <Link className="text-[12px] text-accent-solo hover:underline no-underline" to={`/records/${record.id}`}>
              View →
            </Link>
          </article>
        ))}
      </div>
    </div>
  );
}
