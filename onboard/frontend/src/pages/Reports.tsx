import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName, InsightDomain, Report, ReportType } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { SegmentedControl } from '../components/SegmentedControl';
import { useToast } from '../components/ToastProvider';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';
import { useInsightGenerating } from '../hooks/useInsightGenerating';

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

const TYPE_ORDER: ReportType[] = ['yearly', 'monthly', 'weekly'];

function groupReportsByType(reports: Report[]): Record<ReportType, Report[]> {
  const groups = { weekly: [], monthly: [], yearly: [] } as Record<ReportType, Report[]>;
  for (const report of reports) {
    const type = report.report_type as ReportType;
    if (groups[type]) groups[type].push(report);
  }
  for (const type of TYPE_ORDER) {
    groups[type] = sortByPeriod(groups[type]);
  }
  return groups;
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
  const [activeTab, setActiveTab] = useState('reports');
  // Insight report generating state (module-level, survives navigation)
  const [healthInsightGenerating, setHealthInsightGenerating] = useInsightGenerating('health');
  const [financeInsightGenerating, setFinanceInsightGenerating] = useInsightGenerating('finance');
  const insightGenerating = healthInsightGenerating || financeInsightGenerating;
  const setInsightGenerating = (domain: InsightDomain, value: string | null) => {
    if (domain === 'health') setHealthInsightGenerating(value);
    else setFinanceInsightGenerating(value);
  };
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
    setInsightGenerating(domain, type);
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
      setInsightGenerating(domain, null);
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

  const allClassic = sortByPeriod(data.filter((r) => !(r.metadata?.domain)));

  const insightSections: Record<string, { label: string; icon: string; items: Report[] }> = appName === 'solo' ? {
    health: { label: DOMAIN_LABELS.health, icon: DOMAIN_ICONS.health, items: sortByPeriod(healthInsights || []) },
    finance: { label: DOMAIN_LABELS.finance, icon: DOMAIN_ICONS.finance, items: sortByPeriod(financeInsights || []) },
  } : {};

  const isSolo = appName === 'solo';
  const tabOptions = isSolo
    ? [{ label: 'Reports', value: 'reports' }, { label: 'Health', value: 'health' }, { label: 'Finance', value: 'finance' }]
    : [];

  const classicCount = allClassic.length;
  const insightCount = isSolo ? (healthInsights?.length || 0) + (financeInsights?.length || 0) : 0;
  const totalCount = classicCount + insightCount;

  const TYPE_BADGE: Record<string, string> = { weekly: 'W', monthly: 'M', yearly: 'Y' };
  const TYPE_LABEL: Record<string, string> = { weekly: 'Weekly', monthly: 'Monthly', yearly: 'Yearly' };
  const groupedClassic = groupReportsByType(allClassic);

  function ReportRow({ report, index, onDelete, domain }: { report: Report; index: number; onDelete: (id: string) => void; domain?: InsightDomain }) {
    const period = formatPeriod(report);
    return (
      <div
        className="flex items-center justify-between px-4 py-3 bg-surface-1 hover:bg-surface-2/60 transition-colors animate-[fade-in_0.3s_ease-out_both]"
        style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
      >
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center justify-center w-5 h-5 rounded-md bg-accent-solo/10 text-accent-solo text-[11px] font-mono font-medium">{TYPE_BADGE[report.report_type]}</span>
          {period && <span className="font-mono text-[12px] text-text">{period}</span>}
          <span className="font-mono text-[11px] text-text-muted">
            {period ? `generated ${formatGeneratedTime(report.created_at)}` : formatGeneratedTime(report.created_at)}
          </span>
        </div>
        <span className="inline-flex items-center gap-3">
          <Link to={`/reports/${report.id}`} className="text-[12px] text-accent-solo hover:underline no-underline">Open →</Link>
          <button
            onClick={() => onDelete(report.id)}
            disabled={deleting === report.id}
            className="text-[12px] text-text-muted hover:text-danger cursor-pointer bg-transparent border-none disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            aria-label={`Delete ${domain ? `${domain} ` : ''}report ${period || report.id}`}
          >
            {deleting === report.id ? '…' : '✕'}
          </button>
        </span>
      </div>
    );
  }

  function handleDelete(id: string) {
    setConfirmDeleteId(id);
  }

  function GenerateButton({ label, active, onClick, disabled }: { label: string; active: boolean; onClick: () => void; disabled: boolean }) {
    return (
      <button
        onClick={onClick}
        disabled={disabled}
        className={`text-[12px] px-3 py-1.5 rounded-md border cursor-pointer transition-all active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 ${
          active
            ? 'border-accent-solo/40 bg-accent-solo-dim text-accent-solo'
            : 'border-border bg-surface-2 text-text-secondary hover:text-text hover:border-text-muted'
        }`}
      >
        {active ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 border-[1.5px] border-current border-t-transparent rounded-full animate-spin" />
            generating…
          </span>
        ) : (
          `+ ${label}`
        )}
      </button>
    );
  }

  function renderInsightTab(domain: InsightDomain) {
    const sec = insightSections[domain];
    const domainGenerating = domain === 'health' ? healthInsightGenerating : financeInsightGenerating;
    const anyBusy = generating !== null || insightGenerating !== null;
    const grouped = groupReportsByType(sec.items);
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-text-secondary m-0">{sec.icon} {sec.label}</h3>
          <div className="flex items-center gap-1.5">
            {(['weekly', 'monthly', 'yearly'] as ReportType[]).map((type) => (
              <GenerateButton
                key={type}
                label={TYPE_LABEL[type]}
                active={domainGenerating === type}
                onClick={() => generateInsight(domain, type)}
                disabled={anyBusy}
              />
            ))}
          </div>
        </div>
        {sec.items.length === 0 ? (
          <p className="text-[13px] text-text-muted italic m-0 pl-1">No {domain} insight reports yet.</p>
        ) : (
          <div className="space-y-5">
            {TYPE_ORDER.map((type) => {
              const items = grouped[type];
              if (items.length === 0) return null;
              return (
                <div key={type}>
                  <div className="flex items-center gap-2 mb-2 px-1">
                    <span className="inline-flex items-center justify-center w-5 h-5 rounded-md bg-accent-solo/10 text-accent-solo text-[11px] font-mono font-medium">
                      {TYPE_BADGE[type]}
                    </span>
                    <h4 className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">
                      {TYPE_LABEL[type]}
                    </h4>
                    <span className="text-[11px] text-text-muted">{items.length}</span>
                  </div>
                  <div className="border border-border rounded-lg overflow-hidden divide-y divide-border">
                    {items.map((report, i) => (
                      <ReportRow key={report.id} report={report} index={i} onDelete={handleDelete} domain={domain} />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-2xl text-text m-0">Reports</h2>
        <span className="text-[11px] font-mono text-text-muted">{totalCount} total</span>
      </div>

      {isSolo && (
        <SegmentedControl options={tabOptions} value={activeTab} onChange={setActiveTab} />
      )}

      {genError && (
        <div className="flex items-center gap-2 border border-danger/30 rounded-md bg-danger/5 px-4 py-2.5 text-[13px] text-text" role="alert">
          <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-danger" />
          {genError}
          <button onClick={() => setGenError(null)} className="ml-auto text-text-muted hover:text-text text-[11px] cursor-pointer bg-transparent border-none">dismiss</button>
        </div>
      )}

      {/* Classic reports tab */}
      {(!isSolo || activeTab === 'reports') && (
        <div className="space-y-4">
          <div className="flex items-center gap-1.5">
            {(['weekly', 'monthly', 'yearly'] as ReportType[]).map((type) => (
              <GenerateButton
                key={type}
                label={TYPE_LABEL[type]}
                active={generating === type}
                onClick={() => generate(type)}
                disabled={generating !== null || insightGenerating !== null}
              />
            ))}
          </div>

          {allClassic.length === 0 ? (
            <p className="text-[13px] text-text-muted italic m-0 pl-1">No reports yet. Generate one above.</p>
          ) : (
            <div className="space-y-5">
              {TYPE_ORDER.map((type) => {
                const items = groupedClassic[type];
                if (items.length === 0) return null;
                return (
                  <div key={type}>
                    <div className="flex items-center gap-2 mb-2 px-1">
                      <span className="inline-flex items-center justify-center w-5 h-5 rounded-md bg-accent-solo/10 text-accent-solo text-[11px] font-mono font-medium">
                        {TYPE_BADGE[type]}
                      </span>
                      <h4 className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">
                        {TYPE_LABEL[type]}
                      </h4>
                      <span className="text-[11px] text-text-muted">{items.length}</span>
                    </div>
                    <div className="border border-border rounded-lg overflow-hidden divide-y divide-border">
                      {items.map((report, i) => (
                        <ReportRow key={report.id} report={report} index={i} onDelete={handleDelete} />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Insight tabs (solo only) */}
      {isSolo && activeTab === 'health' && renderInsightTab('health')}
      {isSolo && activeTab === 'finance' && renderInsightTab('finance')}

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete report?"
        description="This action cannot be undone."
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          const id = confirmDeleteId!;
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
