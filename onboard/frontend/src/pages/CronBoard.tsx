import { useMemo } from 'react';

import { api } from '../api/client';
import type { AppName, CronJob } from '../api/types';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function pad(n: number): string {
  return n.toString().padStart(2, '0');
}

function formatTime(hour: number, minute: number): string {
  return `${pad(hour)}:${pad(minute)}`;
}

function parseField(field: string): { type: 'star'; step?: number } | { type: 'value'; values: number[] } | null {
  const trimmed = field.trim();
  if (trimmed === '*') return { type: 'star' };
  if (trimmed.startsWith('*/')) {
    const step = parseInt(trimmed.slice(2), 10);
    if (!Number.isNaN(step)) return { type: 'star', step };
  }
  const values: number[] = [];
  for (const part of trimmed.split(',')) {
    const range = part.split('-').map((s) => parseInt(s, 10));
    if (range.length === 1 && !Number.isNaN(range[0])) {
      values.push(range[0]);
    } else if (range.length === 2 && !Number.isNaN(range[0]) && !Number.isNaN(range[1])) {
      for (let v = range[0]; v <= range[1]; v++) values.push(v);
    }
  }
  if (values.length === 0) return null;
  return { type: 'value', values };
}

function describeCron(expression: string | null | undefined): string | null {
  if (!expression || typeof expression !== 'string') return null;
  const parts = expression.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [minute, hour, dom, month, dow] = parts.map(parseField);
  if (!minute || !hour || !dom || !month || !dow) return null;

  // Every N minutes
  if (hour.type === 'star' && dom.type === 'star' && month.type === 'star' && dow.type === 'star') {
    if (minute.type === 'star' && minute.step === undefined) return 'Every minute';
    if (minute.type === 'star' && minute.step) return `Every ${minute.step} minutes`;
  }

  // Every N hours at :00
  if (minute.type === 'value' && minute.values.length === 1 && minute.values[0] === 0) {
    if (hour.type === 'star' && hour.step && dom.type === 'star' && month.type === 'star' && dow.type === 'star') {
      return `Every ${hour.step} hours`;
    }
    if (hour.type === 'star' && dom.type === 'star' && month.type === 'star' && dow.type === 'star') {
      return 'Every hour';
    }
  }

  // Daily at HH:MM
  if (minute.type === 'value' && hour.type === 'value' && dom.type === 'star' && month.type === 'star' && dow.type === 'star') {
    if (minute.values.length === 1 && hour.values.length === 1) {
      return `Daily at ${formatTime(hour.values[0], minute.values[0])}`;
    }
  }

  // Weekly on specific day(s) at HH:MM
  if (minute.type === 'value' && hour.type === 'value' && dom.type === 'star' && month.type === 'star' && dow.type === 'value') {
    if (minute.values.length === 1 && hour.values.length === 1) {
      const days = dow.values.map((d) => WEEKDAYS[d % 7] ?? d).join(', ');
      return `Weekly on ${days} at ${formatTime(hour.values[0], minute.values[0])}`;
    }
  }

  // Weekdays / weekends at HH:MM
  if (minute.type === 'value' && hour.type === 'value' && dom.type === 'star' && month.type === 'star' && dow.type === 'value') {
    if (minute.values.length === 1 && hour.values.length === 1) {
      const sorted = [...dow.values].sort((a, b) => a - b);
      const isWeekdays = sorted.length === 5 && sorted[0] === 1 && sorted[4] === 5;
      const isWeekends = sorted.length === 2 && sorted[0] === 0 && sorted[1] === 6;
      const time = formatTime(hour.values[0], minute.values[0]);
      if (isWeekdays) return `Weekdays at ${time}`;
      if (isWeekends) return `Weekends at ${time}`;
    }
  }

  // Monthly on specific day(s) at HH:MM
  if (minute.type === 'value' && hour.type === 'value' && dom.type === 'value' && month.type === 'star' && dow.type === 'star') {
    if (minute.values.length === 1 && hour.values.length === 1 && dom.values.length === 1) {
      const day = dom.values[0];
      const suffix = day === 1 ? 'st' : day === 2 ? 'nd' : day === 3 ? 'rd' : 'th';
      return `Monthly on the ${day}${suffix} at ${formatTime(hour.values[0], minute.values[0])}`;
    }
  }

  return null;
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const now = new Date();
  const diffMs = d.getTime() - now.getTime();
  const absSec = Math.abs(Math.floor(diffMs / 1000));
  const isPast = diffMs < 0;

  if (absSec < 60) return isPast ? 'Just now' : 'In a few seconds';
  const absMin = Math.floor(absSec / 60);
  if (absMin < 60) return isPast ? `${absMin}m ago` : `in ${absMin}m`;
  const absHour = Math.floor(absMin / 60);
  if (absHour < 24) return isPast ? `${absHour}h ago` : `in ${absHour}h`;
  const absDay = Math.floor(absHour / 24);
  if (absDay < 7) return isPast ? `${absDay}d ago` : `in ${absDay}d`;
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function jobSummary(job: CronJob): string {
  if (job.command) return job.command;
  if (job.payload && typeof job.payload === 'object') {
    const p = job.payload as Record<string, unknown>;
    if (typeof p.message === 'string') return `Agent: ${p.message}`;
    if (typeof p.kind === 'string') return `${p.kind}`;
    return JSON.stringify(job.payload);
  }
  if (job.notify && typeof job.notify === 'object') {
    return `Notify: ${JSON.stringify(job.notify)}`;
  }
  return 'No details';
}

function StatusBadge({ running }: { running: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium ${
        running
          ? 'bg-success/10 text-success border border-success/30'
          : 'bg-text-muted/10 text-text-muted border border-text-muted/30'
      }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${running ? 'bg-success animate-pulse' : 'bg-text-muted'}`} />
      {running ? 'Scheduler running' : 'Scheduler stopped'}
    </span>
  );
}

function JobCard({ job, index }: { job: CronJob; index: number }) {
  const description = describeCron(job.schedule);
  const summary = jobSummary(job);
  const statusColor =
    job.last_status === 'success'
      ? 'text-success'
      : job.last_status === 'failed' || job.last_status === 'error' || job.last_status === 'timeout'
        ? 'text-danger'
        : 'text-text-muted';
  const name = job.name || 'Unnamed job';
  const schedule = job.schedule || '—';

  return (
    <div
      className="border border-border rounded-lg bg-surface-1 p-4 animate-[fade-in_0.3s_ease-out_both]"
      style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="text-sm font-medium text-text truncate" title={name}>{name}</h3>
            {!job.enabled && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-warning/10 text-warning border border-warning/30">
                Disabled
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
            {description ? (
              <span className="text-accent-solo">{description}</span>
            ) : null}
            <span className="font-mono text-text-muted" title={schedule}>
              {schedule}
            </span>
            {job.timezone && <span className="text-text-muted">({job.timezone})</span>}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[12px] text-text-secondary">Next</div>
          <div className="text-[13px] font-mono text-text" title={job.next_run || ''}>
            {relativeTime(job.next_run)}
          </div>
        </div>
      </div>

      <div className="mt-3 pt-3 border-t border-border flex flex-wrap items-center justify-between gap-3">
        <div className="text-[12px] text-text-secondary truncate max-w-md" title={summary}>
          {summary}
        </div>
        <div className="text-[11px] text-text-muted">
          {job.last_run ? (
            <span>
              Last run{' '}
              <span className={statusColor}>{job.last_status || 'unknown'}</span>{' '}
              <span title={job.last_run}>{relativeTime(job.last_run)}</span>
            </span>
          ) : (
            <span>Never run</span>
          )}
        </div>
      </div>
    </div>
  );
}

export function CronBoard({ appName }: { appName: AppName }) {
  const { data: jobs, error, loading } = useApi(() => api.cron.jobs(appName), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  const { data: status } = useApi(() => api.cron.status(appName), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });

  const { enabled, disabled } = useMemo(() => {
    const all = jobs || [];
    const byName = (a: CronJob, b: CronJob) => (a.name || '').localeCompare(b.name || '');
    return {
      enabled: all.filter((j) => j.enabled).sort(byName),
      disabled: all.filter((j) => !j.enabled).sort(byName),
    };
  }, [jobs]);

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }

  if (error) {
    return (
      <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text" role="alert">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <div>
          <h2 className="font-serif text-2xl text-text m-0">Scheduled Tasks</h2>
          <p className="text-[12px] text-text-muted m-0 mt-1">View-only cron job dashboard</p>
        </div>
        <StatusBadge running={status?.running ?? false} />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="border border-border rounded-lg bg-surface-1 p-3">
          <div className="text-[11px] text-text-muted uppercase tracking-wider">Total jobs</div>
          <div className="text-xl font-mono text-text mt-1">{status?.total_jobs ?? jobs?.length ?? 0}</div>
        </div>
        <div className="border border-border rounded-lg bg-surface-1 p-3">
          <div className="text-[11px] text-text-muted uppercase tracking-wider">Enabled</div>
          <div className="text-xl font-mono text-success mt-1">{status?.enabled_jobs ?? enabled.length}</div>
        </div>
        <div className="border border-border rounded-lg bg-surface-1 p-3">
          <div className="text-[11px] text-text-muted uppercase tracking-wider">Disabled</div>
          <div className="text-xl font-mono text-warning mt-1">{(jobs?.length ?? 0) - (status?.enabled_jobs ?? enabled.length)}</div>
        </div>
      </div>

      {enabled.length === 0 && disabled.length === 0 ? (
        <div className="border border-border rounded-lg bg-surface-1 p-8 text-center">
          <p className="text-[13px] text-text-muted m-0">No scheduled tasks found.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {enabled.length > 0 && (
            <section>
              <h3 className="text-[12px] font-medium text-text-secondary uppercase tracking-wider mb-3 px-1">
                Enabled ({enabled.length})
              </h3>
              <div className="space-y-3">
                {enabled.map((job, i) => (
                  <JobCard key={job.name} job={job} index={i} />
                ))}
              </div>
            </section>
          )}
          {disabled.length > 0 && (
            <section>
              <h3 className="text-[12px] font-medium text-text-secondary uppercase tracking-wider mb-3 px-1">
                Disabled ({disabled.length})
              </h3>
              <div className="space-y-3">
                {disabled.map((job, i) => (
                  <JobCard key={job.name} job={job} index={i} />
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
