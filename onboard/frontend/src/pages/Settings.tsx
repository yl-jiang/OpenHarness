import { api } from '../api/client';
import type { AppName } from '../api/types';
import { StatusBadge } from '../components/StatusBadge';
import { useApi } from '../hooks/useApi';

export function Settings({ appName }: { appName: AppName }) {
  const config = useApi(() => api.config(appName), [appName]);
  const gateway = useApi(() => api.gatewayStatus(appName), [appName]);
  return (
    <div className="page-stack">
      <section className="glass-card detail-card">
        <div className="card-header">
          <h2>Gateway</h2>
          {gateway.data ? <StatusBadge status={gateway.data.status} /> : null}
        </div>
        <div className="button-row">
          <button onClick={() => api.gatewayStart(appName).then(gateway.reload)}>Start</button>
          <button className="danger" onClick={() => api.gatewayStop(appName).then(gateway.reload)}>
            Stop
          </button>
        </div>
        <pre>{JSON.stringify(gateway.data, null, 2)}</pre>
      </section>
      <section className="glass-card detail-card">
        <h2>Config</h2>
        {config.error ? <div className="error-state">{config.error}</div> : null}
        <pre>{JSON.stringify(config.data, null, 2)}</pre>
      </section>
    </div>
  );
}
