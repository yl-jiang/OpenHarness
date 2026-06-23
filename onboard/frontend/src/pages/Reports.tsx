import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName, InsightDomain, Report, ReportType } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

const DOMAIN_LABELS: Record<InsightDomain, string> = {
  health: '♡ Health Insights',
  finance: '$ Finance Insights',
};

const DOMAIN_ICONS: Record<InsightDomain, string> = {
  health: '♡',
  finance: '$',
};

function formatGeneratedTime(raw: string): string {
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

function formatPeriod(report: Report): string {
  const { period_start, period_end, report_type } = report;
  if (!period_start && !period_end) return '';
  const fmtDate = (s: string) => {
    const d = new Date(s + 'T00:00:00');
    if (isNaN(d.getTime())) return s;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };
  if (report_type === 'yearly') {
    const startYear = period_start.slice(0, 4);
    const endYear = period_end.slice(0, 4);
    return startYear === endYear ? startYear : `${startYear} – ${endYear}`;
  }
  if (report_type === 'monthly') {
    // Same month → show single month; cross-month → show range
    const startMonth = period_start.slice(0, 7); // "2026-04"
    const endMonth = period_end.slice(0, 7);
    const fmtYM = (s: string) => {
      const d = new Date(s + 'T00:00:00');
      if (isNaN(d.getTime())) return s;
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short' });
    };
    if (startMonth === endMonth) {
      return fmtYM(period_start);
    }
    return `${fmtYM(period_start)} – ${fmtYM(period_end)}`;
  }
  // weekly
  return `${fmtDate(period_start)} – ${fmtDate(period_end)}`;
}

function sortByPeriod(reports: Report[]): Report[] {
  return [...reports].sort((a, b) => {
    // Sort by period_start desc; fallback to created_at desc
    const pa = a.period_start || a.created_at;
    const pb = b.period_start || b.created_at;
    return pb.localeCompare(pa);
  });
}

// Module-level generating state — survives component unmount (navigation away and back)
const _reportGenerating = new Map<AppName, ReportType | null>();
const _reportGenListeners = new Map<AppName, Set<() => void>>();

function getReportGenerating(app: AppName): ReportType | null {
  return _reportGenerating.get(app) ?? null;
}

function setReportGenerating(app: AppName, value: ReportType | null): void {
  _reportGenerating.set(app, value);
  for (const listener of _reportGenListeners.get(app) ?? []) listener();
}

function useReportGenerating(app: AppName): [ReportType | null, (value: ReportType | null) => void] {
  const [generating, setLocalGenerating] = useState<ReportType | null>(() => getReportGenerating(app));

  useEffect(() => {
    // Re-sync on mount — state may have changed while component was unmounted
    setLocalGenerating(getReportGenerating(app));
    const listener = () => setLocalGenerating(getReportGenerating(app));
    if (!_reportGenListeners.has(app)) _reportGenListeners.set(app, new Set());
    _reportGenListeners.get(app)!.add(listener);
    return () => { _reportGenListeners.get(app)?.delete(listener); };
  }, [app]);

  const setter = useCallback((value: ReportType | null) => setReportGenerating(app, value), [app]);
  return [generating, setter];
}

export function Reports({ appName }: { appName: AppName }) {
  const { data, error, loading, reload } = useApi(() => api.reports(appName), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  // Load insight reports (solo only)
  const { data: healthInsights, reload: reloadHealth } = useApi(
    () => appName === 'solo' ? api.insightReports.list({ domain: 'health' }) : Promise.resolve([]),
    [appName],
    { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS, enabled: appName === 'solo' },
  );
  const { data: financeInsights, reload: reloadFinance } = useApi(
    () => appName === 'solo' ? api.insightReports.list({ domain: 'finance' }) : Promise.resolve([]),
    [appName],
    { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS, enabled: appName === 'solo' },
  );
  const [generating, setGenerating] = useReportGenerating(appName);
  const [genError, setGenError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  // Insight report generating state
  const [insightGenerating, setInsightGenerating] = useState<{ domain: InsightDomain; type: ReportType } | null>(null);
  const { toast } = useToast();

  async function generate(type: ReportType) {
    setGenerating(type);
    setGenError(null);
    try {
      await api.generateReport(appName, type);
      reload();
      toast(`${type} report generated`, 'success');
    } catch (err) {
      const msg = `Failed to generate ${type} report${err instanceof Error ? `: ${err.message}` : ''}`;
      setGenError(msg);
      toast(msg, 'error');
    } finally {
      setGenerating(null);
    }
  }

  async function generateInsight(domain: InsightDomain, type: ReportType) {
    setInsightGenerating({ domain, type });
    setGenError(null);
    try {
      await api.insightReports.generate(domain, type);
      if (domain === 'health') reloadHealth();
      else reloadFinance();
      reload();
      toast(`${DOMAIN_LABELS[domain]} ${type} report generated`, 'success');
    } catch (err) {
      const msg = `Failed to generate ${domain} insight${err instanceof Error ? `: ${err.message}` : ''}`;
      setGenError(msg);
      toast(msg, 'error');
    } finally {
      setInsightGenerating(null);
    }
  }

  async function deleteReport(id: string) {
    setDeleting(id);
    setConfirmDeleteId(null);
    try {
      await api.deleteReport(appName, id);
      reload();
      toast('Report deleted', 'success');
    } catch (err) {
      const msg = `Failed to delete${err instanceof Error ? `: ${err.message}` : ''}`;
      setGenError(msg);
      toast(msg, 'error');
    } finally {
      setDeleting(null);
    }
  }

  async function deleteInsightReport(id: string, domain: InsightDomain) {
    setDeleting(id);
    setConfirmDeleteId(null);
    try {
      await api.insightReports.delete(id);
      if (domain === 'health') reloadHealth();
      else reloadFinance();
      reload();
      toast('Insight report deleted', 'success');
    } catch (err) {
      const msg = `Failed to delete${err instanceof Error ? `: ${err.message}` : ''}`;
      setGenError(msg);
      toast(msg, 'error');
    } finally {
      setDeleting(null);
    }
  }

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text" role="alert">{error ?? 'Failed to load reports.'}</div>;
  }

  const weekly = sortByPeriod(data.filter((r) => r.report_type === 'weekly' && !(r.metadata?.domain)));
  const monthly = sortByPeriod(data.filter((r) => r.report_type === 'monthly' && !(r.metadata?.domain)));
  const yearly = sortByPeriod(data.filter((r) => r.report_type === 'yearly' && !(r.metadata?.domain)));

  const sections: { type: ReportType; label: string; items: Report[] }[] = [
    { type: 'weekly', label: 'Weekly Reports', items: weekly },
    { type: 'monthly', label: 'Monthly Reports', items: monthly },
    { type: 'yearly', label: 'Yearly Reports', items: yearly },
  ];

  // Insight report sections (solo only)
  const insightSections: { domain: InsightDomain; label: string; icon: string; items: Report[] }[] = appName === 'solo' ? [
    { domain: 'health', label: DOMAIN_LABELS.health, icon: DOMAIN_ICONS.health, items: sortByPeriod(healthInsights || []) },
    { domain: 'finance', label: DOMAIN_LABELS.finance, icon: DOMAIN_ICONS.finance, items: sortByPeriod(financeInsights || []) },
  ] : [];

  const classicCount = weekly.length + monthly.length + yearly.length;
  const totalCount = classicCount + (healthInsights?.length || 0) + (financeInsights?.length || 0);

  return (
    <div className="space-y-8">
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-2xl text-text m-0">Reports</h2>
        <span className="text-[11px] font-mono text-text-muted">{totalCount} total</span>
      </div>

      {genError && (
        <div className="flex items-center gap-2 border border-danger/30 rounded-md bg-danger/5 px-4 py-2.5 text-[13px] text-text" role="alert">
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
              disabled={generating !== null || insightGenerating !== null}
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
              {items.map((report, index) => {
                const period = formatPeriod(report);
                return (
                  <div
                    key={report.id}
                    className="flex items-center justify-between px-4 py-3 bg-surface-1 hover:bg-surface-2/60 transition-colors animate-[fade-in_0.3s_ease-out_both]"
                    style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
                  >
                    <div className="flex items-center gap-3">
                      {period && (
                        <span className="font-mono text-[12px] text-text">{period}</span>
                      )}
                      <span className="font-mono text-[11px] text-text-muted">
                        {period ? `generated ${formatGeneratedTime(report.created_at)}` : formatGeneratedTime(report.created_at)}
                      </span>
                    </div>
                    <span className="inline-flex items-center gap-3">
                      <Link to={`/reports/${report.id}`} className="text-[12px] text-accent-solo hover:underline no-underline">Open →</Link>
                      <button
                        onClick={() => setConfirmDeleteId(report.id)}
                        disabled={deleting === report.id}
                        className="text-[12px] text-text-muted hover:text-danger cursor-pointer bg-transparent border-none disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        aria-label={`Delete report ${period || report.id}`}
                      >
                        {deleting === report.id ? '…' : '✕'}
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      ))}

      {/* Insight report sections (solo only) */}
      {insightSections.map(({ domain, label, icon, items }) => (
        <section key={domain} className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-text-secondary m-0">{icon} {label}</h3>
            <div className="flex items-center gap-1.5">
              {(['weekly', 'monthly', 'yearly'] as ReportType[]).map((type) => (
                <button
                  key={type}
                  onClick={() => generateInsight(domain, type)}
                  disabled={generating !== null || insightGenerating !== null}
                  className={`text-[12px] px-2.5 py-1 rounded-md border cursor-pointer transition-all active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 ${
                    insightGenerating?.domain === domain && insightGenerating?.type === type
                      ? 'border-accent-solo/40 bg-accent-solo-dim text-accent-solo'
                      : 'border-border bg-surface-2 text-text-secondary hover:text-text hover:border-text-muted'
                  }`}
                >
                  {insightGenerating?.domain === domain && insightGenerating?.type === type ? (
                    <span className="inline-flex items-center gap-1">
                      <span className="inline-block w-2.5 h-2.5 border-[1.5px] border-current border-t-transparent rounded-full animate-spin" />
                      …
                    </span>
                  ) : (
                    `+ ${type === 'weekly' ? 'W' : type === 'monthly' ? 'M' : 'Y'}`
                  )}
                </button>
              ))}
            </div>
          </div>

          {items.length === 0 ? (
            <p className="text-[13px] text-text-muted italic m-0 pl-1">No {domain} insight reports yet.</p>
          ) : (
            <div className="border border-border rounded-lg overflow-hidden divide-y divide-border">
              {items.map((report, index) => {
                const period = formatPeriod(report);
                const typeLabel = report.report_type === 'weekly' ? 'W' : report.report_type === 'monthly' ? 'M' : 'Y';
                return (
                  <div
                    key={report.id}
                    className="flex items-center justify-between px-4 py-3 bg-surface-1 hover:bg-surface-2/60 transition-colors animate-[fade-in_0.3s_ease-out_both]"
                    style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
                  >
                    <div className="flex items-center gap-3">
                      <span className="inline-flex items-center justify-center w-5 h-5 rounded-md bg-accent-solo/10 text-accent-solo text-[11px] font-mono font-medium">{typeLabel}</span>
                      {period && (
                        <span className="font-mono text-[12px] text-text">{period}</span>
                      )}
                      <span className="font-mono text-[11px] text-text-muted">
                        {period ? `generated ${formatGeneratedTime(report.created_at)}` : formatGeneratedTime(report.created_at)}
                      </span>
                    </div>
                    <span className="inline-flex items-center gap-3">
                      <Link to={`/reports/${report.id}`} className="text-[12px] text-accent-solo hover:underline no-underline">Open →</Link>
                      <button
                        onClick={() => setConfirmDeleteId(report.id)}
                        disabled={deleting === report.id}
                        className="text-[12px] text-text-muted hover:text-danger cursor-pointer bg-transparent border-none disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        aria-label={`Delete insight report ${period || report.id}`}
                      >
                        {deleting === report.id ? '…' : '✕'}
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      ))}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete report?"
        description="This action cannot be undone."
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          const id = confirmDeleteId!;
          // Check if it's an insight report by finding it in insight data
          const isInsight = (healthInsights || []).some(r => r.id === id) || (financeInsights || []).some(r => r.id === id);
          const domain = (healthInsights || []).some(r => r.id === id) ? 'health' as InsightDomain : 'finance' as InsightDomain;
          if (isInsight) deleteInsightReport(id, domain);
          else deleteReport(id);
        }}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </div>
  );
}
