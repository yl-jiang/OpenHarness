import { api } from '../api/client';
import { DataTable } from '../components/DataTable';
import { useApi } from '../hooks/useApi';

export function Highlights() {
  const { data, error, loading } = useApi(() => api.highlights(), []);
  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Failed to load highlights.'}</div>;
  }

  const kindColors: Record<string, string> = {
    blocker: 'text-danger',
    risk: 'text-warning',
    important: 'text-accent-solo',
    prompt: 'text-accent-wolo',
    tool: 'text-text-secondary',
  };

  return (
    <div className="space-y-5">
      <h2 className="font-serif text-2xl text-text m-0">Highlights</h2>
      <DataTable
        rows={data}
        columns={[
          { key: 'kind', title: 'Kind', render: (row) => <span className={`font-mono text-[12px] ${kindColors[row.kind] ?? ''}`}>{row.kind}</span> },
          { key: 'title', title: 'Title', render: (row) => <span className="font-medium text-text">{row.title}</span> },
          { key: 'project', title: 'Project', render: (row) => <span className="font-mono text-[12px]">{row.project}</span> },
          { key: 'content', title: 'Content', render: (row) => <span className="line-clamp-2">{row.content}</span> },
        ]}
      />
    </div>
  );
}
