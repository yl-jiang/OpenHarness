import { api } from '../api/client';
import type { AppName, Entry } from '../api/types';
import { DataTable } from '../components/DataTable';
import { useApi } from '../hooks/useApi';

export function Entries({ appName }: { appName: AppName }) {
  const { data, error, loading } = useApi(() => api.entries(appName, { limit: 100 }), [appName]);
  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Failed to load entries.'}</div>;
  }
  return (
    <div className="page-stack">
      <h2>Entries</h2>
      <DataTable<Entry>
        rows={data.items}
        columns={[
          { key: 'created_at', title: 'Created', render: (row) => row.created_at },
          { key: 'channel', title: 'Channel', render: (row) => row.channel },
          { key: 'content', title: 'Content', render: (row) => <span>{row.content}</span> },
        ]}
      />
    </div>
  );
}
