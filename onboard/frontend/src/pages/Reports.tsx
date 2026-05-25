import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName, ReportType } from '../api/types';
import { DataTable } from '../components/DataTable';
import { useApi } from '../hooks/useApi';

export function Reports({ appName }: { appName: AppName }) {
  const { data, error, loading, reload } = useApi(() => api.reports(appName), [appName]);
  async function generate(type: ReportType) {
    await api.generateReport(appName, type);
    reload();
  }
  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Failed to load reports.'}</div>;
  }
  return (
    <div className="page-stack">
      <div className="card-header">
        <h2>Reports</h2>
        <div className="button-row">
          {(['weekly', 'monthly', 'yearly'] as ReportType[]).map((type) => (
            <button key={type} onClick={() => generate(type)}>
              Generate {type}
            </button>
          ))}
        </div>
      </div>
      <DataTable
        rows={data}
        columns={[
          { key: 'type', title: 'Type', render: (row) => row.report_type },
          { key: 'created', title: 'Created', render: (row) => row.created_at },
          {
            key: 'action',
            title: 'Action',
            render: (row) => <Link to={`/reports/${row.id}`}>Open</Link>,
          },
        ]}
      />
    </div>
  );
}
