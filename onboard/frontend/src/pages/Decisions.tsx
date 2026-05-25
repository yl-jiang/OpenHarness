import { api } from '../api/client';
import { DataTable } from '../components/DataTable';
import { useApi } from '../hooks/useApi';

export function Decisions() {
  const { data, error, loading } = useApi(() => api.decisions(), []);
  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Failed to load decisions.'}</div>;
  }
  return (
    <DataTable
      rows={data}
      columns={[
        { key: 'title', title: 'Title', render: (row) => row.title },
        { key: 'project', title: 'Project', render: (row) => row.project },
        { key: 'rationale', title: 'Rationale', render: (row) => row.rationale },
        { key: 'impact', title: 'Impact', render: (row) => row.impact },
      ]}
    />
  );
}
