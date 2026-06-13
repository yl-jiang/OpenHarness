import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useBlocker } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { StatusBadge } from '../components/StatusBadge';
import { useToast } from '../components/ToastProvider';
import { useApi } from '../hooks/useApi';

/* ─── Tiny form controls (dark-theme, inline) ─────────────────── */

const TIMEZONES = [
  'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Asia/Singapore', 'Asia/Hong_Kong',
  'Asia/Taipei', 'Asia/Kolkata', 'Asia/Dubai', 'Asia/Bangkok',
  'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'America/Sao_Paulo', 'Pacific/Auckland', 'Australia/Sydney', 'UTC',
];

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer shrink-0 ${checked ? 'bg-accent-solo' : 'bg-surface-3'}`}
    >
      <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${checked ? 'translate-x-[18px]' : 'translate-x-[3px]'}`} />
    </button>
  );
}

function NumberField({ value, onChange, min, max, step, suffix }: {
  value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; suffix?: string;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step ?? 1}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-20 px-2 py-1 rounded border border-border bg-surface-2 text-[12px] font-mono text-text text-right tabular-nums focus:outline-none focus:border-accent-solo"
      />
      {suffix && <span className="text-[11px] text-text-muted">{suffix}</span>}
    </div>
  );
}

function TextField({ value, onChange, placeholder }: {
  value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      className="px-2 py-1 rounded border border-border bg-surface-2 text-[12px] font-mono text-text focus:outline-none focus:border-accent-solo w-full max-w-[220px]"
    />
  );
}

function SelectField({ value, onChange, options }: {
  value: string; onChange: (v: string) => void; options: string[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-2 py-1 rounded border border-border bg-surface-2 text-[12px] font-mono text-text focus:outline-none focus:border-accent-solo cursor-pointer"
    >
      {options.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
}

/* ─── Row & Section wrappers ──────────────────────────────────── */

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5 border-b border-border-subtle last:border-0">
      <div className="min-w-0">
        <span className="text-[12px] text-text-secondary">{label}</span>
        {hint && <span className="block text-[10px] text-text-muted mt-0.5">{hint}</span>}
      </div>
      <div className="shrink-0 flex items-center">{children}</div>
    </div>
  );
}

function Card({ title, icon, children, action }: {
  title: string; icon?: string; children: React.ReactNode; action?: React.ReactNode;
}) {
  return (
    <section className="border border-border rounded-lg bg-surface-1 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle">
        <h3 className="text-[13px] font-medium text-text m-0 flex items-center gap-2">
          {icon && <span className="text-text-muted text-[14px]">{icon}</span>}
          {title}
        </h3>
        {action}
      </div>
      <div className="px-4 py-1">{children}</div>
    </section>
  );
}

function CollapsibleCard({ title, icon, defaultOpen, children }: {
  title: string; icon?: string; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <section className="border border-border rounded-lg bg-surface-1 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center justify-between w-full px-4 py-2.5 border-b border-border-subtle cursor-pointer bg-transparent text-left hover:bg-surface-2/50 transition-colors"
      >
        <h3 className="text-[13px] font-medium text-text m-0 flex items-center gap-2">
          {icon && <span className="text-text-muted text-[14px]">{icon}</span>}
          {title}
        </h3>
        <span className="text-text-muted text-[12px] transition-transform" style={{ transform: open ? 'rotate(90deg)' : 'none' }}>
          ›
        </span>
      </button>
      {open && <div className="px-4 py-1">{children}</div>}
    </section>
  );
}

/* ─── Settings Page ───────────────────────────────────────────── */

export function Settings({ appName }: { appName: AppName }) {
  const config = useApi(() => api.config(appName), [appName]);
  const gateway = useApi(() => api.gatewayStatus(appName), [appName]);
  const stats = useApi(() => api.stats(appName), [appName]);
  const { toast } = useToast();

  // Working copy of config for editing
  const [draft, setDraft] = useState<Record<string, any> | null>(null);
  const [saving, setSaving] = useState(false);

  // Initialize draft when config loads
  useEffect(() => {
    if (config.data && !draft) {
      setDraft({ ...config.data });
    }
  }, [config.data, draft]);

  const dirty = useMemo(() => {
    if (!draft || !config.data) return false;
    return JSON.stringify(draft) !== JSON.stringify(config.data);
  }, [draft, config.data]);

  // Helpers to update draft
  const set = useCallback((path: string, value: any) => {
    setDraft((prev) => {
      if (!prev) return prev;
      const next = { ...prev };
      const parts = path.split('.');
      if (parts.length === 1) {
        next[parts[0]] = value;
      } else if (parts.length === 2) {
        next[parts[0]] = { ...next[parts[0]], [parts[1]]: value };
      } else if (parts.length === 3) {
        const a = parts[0], b = parts[1], c = parts[2];
        next[a] = { ...next[a], [b]: { ...(next[a] as any)[b], [c]: value } };
      }
      return next;
    });
  }, []);

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const { workspace: _, ...updates } = draft;
      const result = await api.updateConfig(appName, updates);
      toast('Configuration saved', 'success');
      // Update draft to match server response
      setDraft(result);
    } catch (err) {
      toast(`Save failed${err instanceof Error ? `: ${err.message}` : ''}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (config.data) setDraft({ ...config.data });
  };

  // ── Block navigation when there are unsaved changes ──
  const blocker = useBlocker(dirty);

  // Warn on browser close / refresh
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  // Handle SPA navigation block
  useEffect(() => {
    if (blocker.state === 'blocked') {
      const save = window.confirm('You have unsaved changes. Save before leaving?');
      if (save) {
        (async () => {
          try {
            const { workspace: _, ...updates } = draft ?? {};
            await api.updateConfig(appName, updates);
          } catch {
            // save failed silently, proceed anyway
          }
          blocker.proceed();
        })();
      } else {
        blocker.proceed();
      }
    }
  }, [blocker.state, draft, appName]);

  // Gateway actions
  async function handleStart() {
    try {
      await api.gatewayStart(appName);
      gateway.reload();
      toast('Gateway started', 'success');
    } catch (err) {
      toast(`Failed: ${err instanceof Error ? err.message : 'unknown error'}`, 'error');
    }
  }

  async function handleStop() {
    try {
      await api.gatewayStop(appName);
      gateway.reload();
      toast('Gateway stopped', 'success');
    } catch (err) {
      toast(`Failed: ${err instanceof Error ? err.message : 'unknown error'}`, 'error');
    }
  }

  async function handleProcess() {
    try {
      const result = await api.process(appName);
      toast(`Processed ${result.processed ?? 0} entries`, 'success');
    } catch (err) {
      toast(`Process failed: ${err instanceof Error ? err.message : 'unknown error'}`, 'error');
    }
  }

  async function handleScan() {
    try {
      const result = await api.scanProjects(appName);
      toast(`Scan done: ${result.created} new projects`, 'success');
    } catch (err) {
      toast(`Scan failed: ${err instanceof Error ? err.message : 'unknown error'}`, 'error');
    }
  }

  const gw = gateway.data;
  const cfg = draft;
  const hb = cfg?.heartbeat as Record<string, any> | undefined;
  const fd = cfg?.feed_digest as Record<string, any> | undefined;
  const st = stats.data;

  if (config.loading) {
    return (
      <div className="space-y-4 animate-pulse max-w-3xl">
        <div className="h-8 w-32 rounded bg-surface-2" />
        <div className="h-48 rounded-lg bg-surface-2" />
        <div className="h-48 rounded-lg bg-surface-2" />
      </div>
    );
  }

  return (
    <div className="space-y-4 max-w-3xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-2xl text-text m-0">Settings</h2>
        {dirty && (
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-warning">unsaved changes</span>
            <button onClick={handleReset} className="text-[11px] px-2.5 py-1 rounded border border-border text-text-muted hover:text-text hover:bg-surface-2 transition-colors cursor-pointer">
              Reset
            </button>
            <button onClick={handleSave} disabled={saving} className="text-[11px] px-3 py-1 rounded border border-accent-solo/40 bg-accent-solo/10 text-accent-solo hover:bg-accent-solo/20 active:scale-[0.97] transition-all cursor-pointer disabled:opacity-50">
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        )}
      </div>

      {/* System Overview */}
      <Card title="Overview" icon="◇">
        <Row label="Workspace">
          <span className="text-[12px] font-mono text-text-muted">{cfg?.workspace ?? '—'}</span>
        </Row>
        <Row label="Provider Profile" hint="LLM provider used by gateway">
          <SelectField
            value={cfg?.provider_profile ?? 'deepseek'}
            onChange={(v) => set('provider_profile', v)}
            options={[
              'claude-api', 'claude-subscription', 'openai-compatible', 'codex',
              'copilot', 'moonshot', 'gemini', 'minimax', 'nvidia',
              'deepseek', 'qwen', 'xiaomi',
            ]}
          />
        </Row>
        <Row label="Log Level">
          <SelectField value={cfg?.log_level ?? 'INFO'} onChange={(v) => set('log_level', v)} options={['DEBUG', 'INFO', 'WARNING', 'ERROR']} />
        </Row>
        {st && (
          <>
            <Row label="Current Model">
              <span className="text-[12px] font-mono text-text-secondary">{st.current_model ?? '—'}</span>
            </Row>
            {st.vision_model && (
              <Row label="Vision Model">
                <span className="text-[12px] font-mono text-text-secondary">{st.vision_model}</span>
              </Row>
            )}
          </>
        )}
      </Card>

      {/* Gateway */}
      <Card
        title="Gateway"
        icon="⊙"
        action={gw ? <StatusBadge status={gw.status} /> : undefined}
      >
        <div className="flex items-center gap-2 py-2.5">
          <button
            onClick={handleStart}
            disabled={gw?.status === 'running'}
            className="text-[12px] px-3 py-1.5 rounded-md border border-success/30 bg-success/10 text-success cursor-pointer hover:bg-success/20 active:scale-[0.97] transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Start
          </button>
          <button
            onClick={handleStop}
            disabled={gw?.status !== 'running'}
            className="text-[12px] px-3 py-1.5 rounded-md border border-danger/30 bg-danger/10 text-danger cursor-pointer hover:bg-danger/20 active:scale-[0.97] transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Stop
          </button>
        </div>
        {gw && (
          <>
            {gw.pid && <Row label="PID"><span className="text-[12px] font-mono text-text-secondary">{gw.pid}</span></Row>}
            {gw.enabled_channels && gw.enabled_channels.length > 0 && (
              <Row label="Channels">
                <div className="flex gap-1.5 flex-wrap justify-end">
                  {gw.enabled_channels.map((ch: string) => (
                    <span key={ch} className="text-[11px] font-mono px-2 py-0.5 rounded border border-border-subtle bg-surface-2 text-text-muted">{ch}</span>
                  ))}
                </div>
              </Row>
            )}
            {gw.last_error && (
              <div className="py-2.5">
                <span className="text-[11px] text-danger font-mono block break-all">{gw.last_error}</span>
              </div>
            )}
          </>
        )}
      </Card>

      {/* LLM Channels */}
      <Card title="LLM & Channels" icon="◎">
        <Row label="Send Progress" hint="Include progress updates in responses">
          <Toggle checked={cfg?.send_progress ?? true} onChange={(v) => set('send_progress', v)} />
        </Row>
        <Row label="Send Tool Hints" hint="Include tool usage hints in responses">
          <Toggle checked={cfg?.send_tool_hints ?? true} onChange={(v) => set('send_tool_hints', v)} />
        </Row>
      </Card>

      {/* Heartbeat */}
      <CollapsibleCard title="Heartbeat" icon="♡" defaultOpen>
        <Row label="Enabled" hint="Periodic check-in for the gateway">
          <Toggle checked={hb?.enabled ?? true} onChange={(v) => set('heartbeat.enabled', v)} />
        </Row>
        <Row label="Interval" hint="Time between heartbeats">
          <NumberField value={Math.round((hb?.interval_s ?? 1800) / 60)} onChange={(v) => set('heartbeat.interval_s', v * 60)} min={1} suffix="min" />
        </Row>
        <Row label="Quiet Hours" hint="No pushes during this window">
          <div className="flex items-center gap-1.5">
            <TextField value={hb?.quiet_hours_start ?? '22:30'} onChange={(v) => set('heartbeat.quiet_hours_start', v)} placeholder="22:30" />
            <span className="text-[11px] text-text-muted">–</span>
            <TextField value={hb?.quiet_hours_end ?? '08:00'} onChange={(v) => set('heartbeat.quiet_hours_end', v)} placeholder="08:00" />
          </div>
        </Row>
        <Row label="Timezone">
          <SelectField value={hb?.timezone ?? 'Asia/Shanghai'} onChange={(v) => set('heartbeat.timezone', v)} options={TIMEZONES} />
        </Row>
        <Row label="Max Daily Pushes">
          <NumberField value={hb?.max_daily_pushes ?? 3} onChange={(v) => set('heartbeat.max_daily_pushes', v)} min={0} max={20} />
        </Row>
        <Row label="Keep Recent Messages">
          <NumberField value={hb?.keep_recent_messages ?? 8} onChange={(v) => set('heartbeat.keep_recent_messages', v)} min={1} max={50} />
        </Row>
      </CollapsibleCard>

      {/* Feed Digest */}
      <CollapsibleCard title="Feed Digest" icon="◈">
        <Row label="Enabled">
          <Toggle checked={fd?.enabled ?? true} onChange={(v) => set('feed_digest.enabled', v)} />
        </Row>
        <Row label="Schedule" hint="Cron expression (e.g. 30 21 * * *)">
          <TextField value={fd?.schedule ?? '30 21 * * *'} onChange={(v) => set('feed_digest.schedule', v)} />
        </Row>
        <Row label="Timezone">
          <SelectField value={fd?.timezone ?? 'Asia/Shanghai'} onChange={(v) => set('feed_digest.timezone', v)} options={TIMEZONES} />
        </Row>
        <Row label="Lookback" hint="How far back to scan for new items">
          <NumberField value={fd?.lookback_hours ?? 24} onChange={(v) => set('feed_digest.lookback_hours', v)} min={1} suffix="hours" />
        </Row>
        <Row label="Max Candidates">
          <NumberField value={fd?.max_candidates ?? 90} onChange={(v) => set('feed_digest.max_candidates', v)} min={1} />
        </Row>
        <Row label="Max Items">
          <NumberField value={fd?.max_items ?? 30} onChange={(v) => set('feed_digest.max_items', v)} min={1} />
        </Row>
        <Row label="Max Trends">
          <NumberField value={fd?.max_trends ?? 8} onChange={(v) => set('feed_digest.max_trends', v)} min={1} />
        </Row>
        <Row label="Min Relevance Score">
          <NumberField value={fd?.min_relevance_score ?? 0.3} onChange={(v) => set('feed_digest.min_relevance_score', v)} min={0} max={1} step={0.05} />
        </Row>
        <Row label="Min Signal Score">
          <NumberField value={fd?.min_signal_score ?? 0.2} onChange={(v) => set('feed_digest.min_signal_score', v)} min={0} max={1} step={0.05} />
        </Row>
        <Row label="Archive Enabled">
          <Toggle checked={fd?.archive_enabled ?? true} onChange={(v) => set('feed_digest.archive_enabled', v)} />
        </Row>
        <Row label="IM Push Enabled">
          <Toggle checked={fd?.im_push_enabled ?? true} onChange={(v) => set('feed_digest.im_push_enabled', v)} />
        </Row>
      </CollapsibleCard>

      {/* Actions */}
      <Card title="Actions" icon="⊞">
        <div className="flex flex-wrap gap-2 py-3">
          <button
            onClick={handleProcess}
            className="text-[12px] px-3 py-1.5 rounded-md border border-border bg-surface-2 text-text-secondary hover:bg-surface-3 hover:text-text cursor-pointer transition-colors"
          >
            Process Pending
          </button>
          <button
            onClick={handleScan}
            className="text-[12px] px-3 py-1.5 rounded-md border border-border bg-surface-2 text-text-secondary hover:bg-surface-3 hover:text-text cursor-pointer transition-colors"
          >
            Scan for Projects
          </button>
          <button
            onClick={() => { gateway.reload(); config.reload(); stats.reload(); }}
            className="text-[12px] px-3 py-1.5 rounded-md border border-border bg-surface-2 text-text-secondary hover:bg-surface-3 hover:text-text cursor-pointer transition-colors"
          >
            Refresh All
          </button>
        </div>
        {st && (
          <div className="grid grid-cols-3 gap-3 py-2.5 border-t border-border-subtle">
            <div className="text-center">
              <div className="text-[16px] font-mono text-text tabular-nums">{st.total_records.toLocaleString()}</div>
              <div className="text-[10px] text-text-muted mt-0.5">Records</div>
            </div>
            <div className="text-center">
              <div className="text-[16px] font-mono text-text tabular-nums">{st.pending_todos.toLocaleString()}</div>
              <div className="text-[10px] text-text-muted mt-0.5">Pending</div>
            </div>
            <div className="text-center">
              <div className="text-[16px] font-mono text-text tabular-nums">{st.llm_total_calls.toLocaleString()}</div>
              <div className="text-[10px] text-text-muted mt-0.5">LLM Calls</div>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
