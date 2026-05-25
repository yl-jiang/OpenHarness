import { api } from '../api/client';
import { DataTable } from '../components/DataTable';
import { useApi } from '../hooks/useApi';

export function Highlights() {
  const { data, error, loading } = useApi(() => api.highlights(), []);
  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Failed to load highlights.'}</div>;
  }
  return (
    <DataTable
      rows={data}
      columns={[
        { key: 'kind', title: 'Kind', render: (row) => row.kind },
        { key: 'title', title: 'Title', render: (row) => row.title },
        { key: 'project', title: 'Project', render: (row) => row.project },
        { key: 'content', title: 'Content', render: (row) => row.content },
      ]}
    />
  );
}
