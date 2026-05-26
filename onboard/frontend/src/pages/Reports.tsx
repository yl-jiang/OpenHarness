import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName, Report, ReportType } from '../api/types';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

function formatTime(raw: string): string {
  const d = new Date(raw);
  if (isNaN(d.getTime())) return raw;
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / 86400000);

  let date: string;
  if (diffDays === 0) date = 'Today';
  else if (diffDays === 1) date = 'Yesterday';
  else if (diffDays < 7) date = d.toLocaleDateString(undefined, { weekday: 'short' });
  else date = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
  return `${date} ${time}`;
}

function sortByNewest(reports: Report[]): Report[] {
  return [...reports].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

export function Reports({ appName }: { appName: AppName }) {
  const { data, error, loading, reload } = useApi(() => api.reports(appName), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  const [generating, setGenerating] = useState<ReportType | null>(null);
  const [genError, setGenError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  async function generate(type: ReportType) {
    setGenerating(type);
    setGenError(null);
    try {
      await api.generateReport(appName, type);
      reload();
    } catch (err) {
      setGenError(`Failed to generate ${type} report${err instanceof Error ? `: ${err.message}` : ''}`);
    } finally {
      setGenerating(null);
    }
  }

  async function deleteReport(id: string) {
    if (!confirm('Delete this report permanently?')) return;
    setDeleting(id);
    try {
      await api.deleteReport(appName, id);
      reload();
    } catch (err) {
      setGenError(`Failed to delete${err instanceof Error ? `: ${err.message}` : ''}`);
    } finally {
      setDeleting(null);
    }
  }

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Failed to load reports.'}</div>;
  }

  const weekly = sortByNewest(data.filter((r) => r.report_type === 'weekly'));
  const monthly = sortByNewest(data.filter((r) => r.report_type === 'monthly'));
  const yearly = sortByNewest(data.filter((r) => r.report_type === 'yearly'));

  const sections: { type: ReportType; label: string; items: Report[] }[] = [
    { type: 'weekly', label: 'Weekly Reports', items: weekly },
    { type: 'monthly', label: 'Monthly Reports', items: monthly },
    { type: 'yearly', label: 'Yearly Reports', items: yearly },
  ];

  return (
    <div className="space-y-8">
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-2xl text-text m-0">Reports</h2>
        <span className="text-[11px] font-mono text-text-muted">{data.length} total</span>
      </div>

      {genError && (
        <div className="flex items-center gap-2 border border-danger/30 rounded-md bg-danger/5 px-4 py-2.5 text-[13px] text-text">
          <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-danger" />
          {genError}
          <button onClick={() => setGenError(null)} className="ml-auto text-text-muted hover:text-text text-[11px] cursor-pointer bg-transparent border-none">dismiss</button>
        </div>
      )}

      {sections.map(({ type, label, items }) => (
        <section key={type} className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-text-secondary m-0">{label}</h3>
            <button
              onClick={() => generate(type)}
              disabled={generating !== null}
              className={`text-[12px] px-3 py-1.5 rounded-md border cursor-pointer transition-all active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 ${
                generating === type
                  ? 'border-accent-solo/40 bg-accent-solo-dim text-accent-solo'
                  : 'border-border bg-surface-2 text-text-secondary hover:text-text hover:border-text-muted'
              }`}
            >
              {generating === type ? (
                <span className="inline-flex items-center gap-1.5">
                  <span className="inline-block w-3 h-3 border-[1.5px] border-current border-t-transparent rounded-full animate-spin" />
                  generating…
                </span>
              ) : (
                `+ ${type}`
              )}
            </button>
          </div>

          {items.length === 0 ? (
            <p className="text-[13px] text-text-muted italic m-0 pl-1">No {type} reports yet.</p>
          ) : (
            <div className="border border-border rounded-lg overflow-hidden divide-y divide-border">
              {items.map((report) => (
                <div key={report.id} className="flex items-center justify-between px-4 py-3 bg-surface-1 hover:bg-surface-2/60 transition-colors">
                  <span className="font-mono text-[12px] text-text-secondary">{formatTime(report.created_at)}</span>
                  <span className="inline-flex items-center gap-3">
                    <Link to={`/reports/${report.id}`} className="text-[12px] text-accent-solo hover:underline no-underline">Open →</Link>
                    <button
                      onClick={() => deleteReport(report.id)}
                      disabled={deleting === report.id}
                      className="text-[12px] text-text-muted hover:text-danger cursor-pointer bg-transparent border-none disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      {deleting === report.id ? '…' : '✕'}
                    </button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
