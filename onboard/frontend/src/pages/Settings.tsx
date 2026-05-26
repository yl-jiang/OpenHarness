import { api } from '../api/client';
import type { AppName } from '../api/types';
import { StatusBadge } from '../components/StatusBadge';
import { useApi } from '../hooks/useApi';

export function Settings({ appName }: { appName: AppName }) {
  const config = useApi(() => api.config(appName), [appName]);
  const gateway = useApi(() => api.gatewayStatus(appName), [appName]);
  return (
    <div className="space-y-5 max-w-3xl">
      <h2 className="font-serif text-2xl text-text m-0">Settings</h2>

      {/* Gateway */}
      <section className="border border-border rounded-lg bg-surface-1 p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium text-text m-0">Gateway</h3>
          {gateway.data ? <StatusBadge status={gateway.data.status} /> : null}
        </div>
        <div className="flex gap-2 mb-4">
          <button
            onClick={() => api.gatewayStart(appName).then(gateway.reload)}
            className="text-[12px] px-3 py-1.5 rounded-md border border-success/30 bg-success/10 text-success cursor-pointer hover:bg-success/20 active:scale-[0.97] transition-all"
          >
            Start
          </button>
          <button
            onClick={() => api.gatewayStop(appName).then(gateway.reload)}
            className="text-[12px] px-3 py-1.5 rounded-md border border-danger/30 bg-danger/10 text-danger cursor-pointer hover:bg-danger/20 active:scale-[0.97] transition-all"
          >
            Stop
          </button>
        </div>
        <pre className="text-[12px] font-mono text-text-secondary bg-surface-2 border border-border rounded-md p-3 overflow-x-auto">
          {JSON.stringify(gateway.data, null, 2)}
        </pre>
      </section>

      {/* Config */}
      <section className="border border-border rounded-lg bg-surface-1 p-5">
        <h3 className="text-sm font-medium text-text m-0 mb-4">Configuration</h3>
        {config.error ? (
          <div className="border border-danger/30 rounded-lg bg-danger/5 p-4 text-sm text-text mb-3">{config.error}</div>
        ) : null}
        <pre className="text-[12px] font-mono text-text-secondary bg-surface-2 border border-border rounded-md p-3 overflow-x-auto">
          {JSON.stringify(config.data, null, 2)}
        </pre>
      </section>
    </div>
  );
}
