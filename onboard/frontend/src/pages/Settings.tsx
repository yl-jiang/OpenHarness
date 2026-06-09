import { api } from '../api/client';
import type { AppName } from '../api/types';
import { StatusBadge } from '../components/StatusBadge';
import { useToast } from '../components/ToastProvider';
import { useApi } from '../hooks/useApi';

function ConfigRow({ label, value }: { label: string; value: unknown }) {
  if (value === undefined || value === null) return null;
  const display = typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  return (
    <div className="flex items-start justify-between gap-4 py-2.5 border-b border-border-subtle last:border-0">
      <span className="text-[12px] text-text-muted uppercase tracking-wider shrink-0 w-32">{label}</span>
      <span className="text-[13px] font-mono text-text-secondary text-right break-all">{display}</span>
    </div>
  );
}

export function Settings({ appName }: { appName: AppName }) {
  const config = useApi(() => api.config(appName), [appName]);
  const gateway = useApi(() => api.gatewayStatus(appName), [appName]);
  const { toast } = useToast();

  async function handleStart() {
    try {
      await api.gatewayStart(appName);
      gateway.reload();
      toast('Gateway started', 'success');
    } catch (err) {
      toast(`Failed to start gateway${err instanceof Error ? `: ${err.message}` : ''}`, 'error');
    }
  }

  async function handleStop() {
    try {
      await api.gatewayStop(appName);
      gateway.reload();
      toast('Gateway stopped', 'success');
    } catch (err) {
      toast(`Failed to stop gateway${err instanceof Error ? `: ${err.message}` : ''}`, 'error');
    }
  }

  const gw = gateway.data;

  return (
    <div className="space-y-5 max-w-3xl">
      <h2 className="font-serif text-2xl text-text m-0">Settings</h2>

      {/* Gateway */}
      <section className="border border-border rounded-lg bg-surface-1 p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium text-text m-0">Gateway</h3>
          {gw ? <StatusBadge status={gw.status} /> : null}
        </div>
        <div className="flex gap-2 mb-5">
          <button
            onClick={handleStart}
            className="text-[12px] px-3 py-1.5 rounded-md border border-success/30 bg-success/10 text-success cursor-pointer hover:bg-success/20 active:scale-[0.97] transition-all"
          >
            Start
          </button>
          <button
            onClick={handleStop}
            className="text-[12px] px-3 py-1.5 rounded-md border border-danger/30 bg-danger/10 text-danger cursor-pointer hover:bg-danger/20 active:scale-[0.97] transition-all"
          >
            Stop
          </button>
        </div>
        {gw ? (
          <div className="divide-y divide-border-subtle">
            <ConfigRow label="Status" value={gw.status} />
            <ConfigRow label="PID" value={gw.pid} />
            <ConfigRow label="Port" value={gw.port} />
            <ConfigRow label="Uptime" value={gw.uptime_seconds != null ? `${gw.uptime_seconds}s` : '—'} />
            {gw.provider_profile && <ConfigRow label="Provider" value={gw.provider_profile} />}
            {gw.enabled_channels && gw.enabled_channels.length > 0 && (
              <ConfigRow label="Channels" value={gw.enabled_channels.join(', ')} />
            )}
            {gw.last_error && (
              <div className="py-2.5">
                <span className="text-[12px] text-text-muted uppercase tracking-wider block mb-1">Last error</span>
                <span className="text-[12px] font-mono text-danger">{gw.last_error}</span>
              </div>
            )}
          </div>
        ) : (
          <pre className="text-[12px] font-mono text-text-secondary bg-surface-2 border border-border rounded-md p-3 overflow-x-auto">
            {JSON.stringify(gateway.data, null, 2)}
          </pre>
        )}
      </section>

      {/* Config */}
      <section className="border border-border rounded-lg bg-surface-1 p-5">
        <h3 className="text-sm font-medium text-text m-0 mb-4">Configuration</h3>
        {config.error ? (
          <div className="border border-danger/30 rounded-lg bg-danger/5 p-4 text-sm text-text mb-3" role="alert">{config.error}</div>
        ) : null}
        {config.data && typeof config.data === 'object' ? (
          <div className="divide-y divide-border-subtle">
            {Object.entries(config.data).map(([key, value]) => (
              <ConfigRow key={key} label={key} value={value} />
            ))}
          </div>
        ) : (
          <pre className="text-[12px] font-mono text-text-secondary bg-surface-2 border border-border rounded-md p-3 overflow-x-auto">
            {JSON.stringify(config.data, null, 2)}
          </pre>
        )}
      </section>
    </div>
  );
}
