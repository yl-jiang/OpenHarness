import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName, InsightDomain, InsightReportJSON, InsightBlindSpot, InsightItem, InsightPattern, InsightRecommendation, InsightMetric, InsightPeriodComparison } from '../api/types';
import { Breadcrumb } from '../components/Breadcrumb';
import { MarkdownView } from '../components/MarkdownView';
import { useApi } from '../hooks/useApi';

interface TocItem {
  id: string;
  level: number;
  text: string;
}

function formatCreatedAt(raw: string): string {
  const d = new Date(raw);
  if (isNaN(d.getTime())) return raw;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'section';
}

function extractHeadings(content: string): TocItem[] {
  const lines = content.split('\n');
  const items: TocItem[] = [];
  const seen = new Map<string, number>();
  let inFence = false;
  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (/^```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const match = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!match) continue;
    const level = match[1].length;
    const text = match[2].replace(/`/g, '').replace(/\*\*/g, '').trim();
    if (!text) continue;
    const base = slugify(text);
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    const id = count === 0 ? base : `${base}-${count}`;
    items.push({ id, level, text });
  }
  return items;
}


// ── Insight report structured rendering ──────────────────────────

const SEVERITY_STYLES: Record<string, string> = {
  alert: 'border-l-4 border-l-danger bg-danger/5 text-danger',
  watch: 'border-l-4 border-l-warning bg-warning/5 text-warning',
  info: 'border-l-4 border-l-accent-solo bg-accent-solo/5 text-accent-solo',
};

const SEVERITY_ICONS: Record<string, string> = {
  alert: '🔴',
  watch: '🟡',
  info: 'ℹ️',
};

const DIRECTION_ARROWS: Record<string, string> = { up: '↑', down: '↓', flat: '→' };
const STRENGTH_DOTS: Record<string, string> = { strong: '●●●', moderate: '●●○', weak: '●○○' };

function Sparkline({ data }: { data: number[] }) {
  if (!data || data.length === 0) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const chars = '▁▂▃▄▅▆▇█';
  return (
    <span className="font-mono text-xs text-text-muted tracking-wider">
      {data.map((v, i) => {
        const idx = Math.round(((v - min) / range) * (chars.length - 1));
        return <span key={i}>{chars[idx]}</span>;
      })}
    </span>
  );
}

function InsightHeroBand({ insight, domain, reportType }: { insight: InsightReportJSON; domain: string; reportType: string }) {
  const domainLabel = domain === 'health' ? 'Health' : 'Finance';
  const periodLabel = { weekly: 'Weekly', monthly: 'Monthly', yearly: 'Yearly' }[reportType] || reportType;
  const comparisons = insight.period_comparison || [];
  return (
    <section className="p-5 rounded-lg border border-border bg-surface-1">
      <h2 className="text-lg font-serif text-text mb-2">
        {domain === 'health' ? '♡' : '$'} {domainLabel} {periodLabel} Insight
      </h2>
      {insight.headline && <p className="text-base font-medium text-text mb-2">{insight.headline}</p>}
      {insight.narrative && <p className="text-sm text-text-muted mb-4">{insight.narrative}</p>}
      {comparisons.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {comparisons.map((c: InsightPeriodComparison, i: number) => {
            const arrow = DIRECTION_ARROWS[c.direction] || '';
            const isUp = c.direction === 'up';
            return (
              <span key={i} className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-mono ${
                isUp ? 'bg-danger/10 text-danger' : c.direction === 'down' ? 'bg-success/10 text-success' : 'bg-surface-2 text-text-muted'
              }`}>
                {c.metric} {c.current}{c.unit || ''} {arrow}{Math.abs(c.delta_pct).toFixed(1)}%
              </span>
            );
          })}
        </div>
      )}
    </section>
  );
}

function InsightBlindSpots({ blindSpots }: { blindSpots: InsightBlindSpot[] }) {
  if (!blindSpots.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">🕳️ Blind Spots</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {blindSpots.map((bs, i) => (
          <div key={i} className={`p-4 rounded-lg ${SEVERITY_STYLES[bs.severity] || SEVERITY_STYLES.info}`}>
            <p className="font-medium text-sm mb-1">{SEVERITY_ICONS[bs.severity] || 'ℹ️'} {bs.title}</p>
            <p className="text-xs opacity-80 mb-1">{bs.why}</p>
            <p className="text-xs opacity-60">Evidence: {bs.evidence}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function InsightMetrics({ metrics }: { metrics: InsightMetric[] }) {
  if (!metrics || !metrics.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">📈 Key Metrics</h3>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {metrics.map((m, i) => (
          <div key={i} className="p-4 rounded-lg border border-border bg-surface-1">
            <p className="text-xs text-text-muted mb-1">{m.label}</p>
            <p className="text-xl font-mono text-text">{m.value}<span className="text-xs text-text-muted ml-1">{m.unit}</span></p>
            {m.trend && m.trend.length > 0 && <div className="mt-2"><Sparkline data={m.trend} /></div>}
            {m.comparison_value != null && <p className="text-xs text-text-muted mt-1">vs {m.comparison_label || 'prev'} {m.comparison_value}{m.unit}</p>}
          </div>
        ))}
      </div>
    </section>
  );
}

function InsightInsights({ insights }: { insights: InsightItem[] }) {
  if (!insights.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">🔍 Deep Insights</h3>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {insights.map((ins, i) => (
          <div key={i} className={`p-4 rounded-lg border border-border bg-surface-1 ${SEVERITY_STYLES[ins.severity] || ''}`}>
            <p className="font-medium text-sm mb-2">{ins.icon || '🔍'} {ins.title}</p>
            <p className="text-xs text-text-muted mb-2">{ins.analysis}</p>
            {ins.evidence && ins.evidence.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {ins.evidence.map((e, j) => (
                  <span key={j} className="px-1.5 py-0.5 rounded text-[10px] bg-surface-2 text-text-muted font-mono">{e}</span>
                ))}
              </div>
            )}
            <div className="flex items-center gap-2">
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                ins.severity === 'alert' ? 'bg-danger/10 text-danger' :
                ins.severity === 'watch' ? 'bg-warning/10 text-warning' :
                'bg-accent-solo/10 text-accent-solo'
              }`}>{ins.severity}</span>
              {ins.tags && ins.tags.map((t, j) => <span key={j} className="text-[10px] text-text-muted">#{t}</span>)}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function InsightPatterns({ patterns }: { patterns: InsightPattern[] }) {
  if (!patterns || !patterns.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">🔗 Patterns</h3>
      <div className="flex flex-wrap gap-2">
        {patterns.map((p, i) => (
          <span key={i} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-border bg-surface-1 text-xs">
            <span className="font-medium text-text">{p.name}</span>
            <span className="text-text-muted">[{STRENGTH_DOTS[p.strength] || '●○○'}]</span>
            <span className="text-text-muted">{p.detail}</span>
          </span>
        ))}
      </div>
    </section>
  );
}

function InsightRecommendations({ recommendations }: { recommendations: InsightRecommendation[] }) {
  if (!recommendations.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">💡 Recommendations</h3>
      <ol className="space-y-2">
        {recommendations.map((r, i) => (
          <li key={i} className="p-3 rounded-lg border border-border bg-surface-1 text-sm">
            <p className="font-medium text-text">{i + 1}. {r.action}</p>
            <p className="text-xs text-text-muted mt-1">{r.rationale}</p>
            <p className="text-xs text-text-muted">Signal: {r.expected_signal}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ReportView({ appName }: { appName: AppName }) {
  const { id = '' } = useParams();
  const { data, error, loading } = useApi(() => api.report(appName, id), [appName, id]);
  const [activeId, setActiveId] = useState<string>('');

  const headings = useMemo(() => (data?.content ? extractHeadings(data.content) : []), [data?.content]);

  useEffect(() => {
    if (!data?.content) return;
    const els = headings
      .map((h) => document.getElementById(h.id))
      .filter((el): el is HTMLElement => el !== null);
    if (els.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActiveId(visible[0].target.id);
      },
      { rootMargin: '-80px 0px -70% 0px', threshold: 0 },
    );
    els.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [data?.content, headings]);

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Report not found.'}</div>;
  }

  const periodLabel =
    data.period_start && data.period_end
      ? data.period_start === data.period_end
        ? data.period_start
        : `${data.period_start} → ${data.period_end}`
      : null;

  const metadata = data.metadata || {};
  const domain = metadata.domain as InsightDomain | undefined;
  const insightJson = metadata.insight_json as InsightReportJSON | undefined;
  const isInsight = !!domain && !!insightJson;

  if (isInsight) {
    // Structured insight report rendering
    const domainLabel = domain === 'health' ? 'Health' : 'Finance';
    const domainIcon = domain === 'health' ? '♡' : '$';
    return (
      <div className="max-w-5xl space-y-6">
        <Breadcrumb items={[
          { label: 'Reports', to: '/reports' },
          { label: `${domainIcon} ${domainLabel} ${data.report_type} Insight` },
        ]} />
        <InsightHeroBand insight={insightJson} domain={domain!} reportType={data.report_type} />
        <InsightBlindSpots blindSpots={insightJson.blind_spots || []} />
        <InsightMetrics metrics={insightJson.metrics || []} />
        <InsightInsights insights={insightJson.insights || []} />
        <InsightPatterns patterns={insightJson.patterns || []} />
        <InsightRecommendations recommendations={insightJson.recommendations || []} />
        <div className="border-t border-border pt-3">
          <details>
            <summary className="text-xs text-text-muted cursor-pointer">Raw Markdown</summary>
            <div className="mt-2 p-4 rounded-lg bg-surface-2 text-xs font-mono text-text-muted overflow-auto max-h-96">
              <MarkdownView content={data.content} />
            </div>
          </details>
        </div>
      </div>
    );
  }

  // Classic report rendering
  return (
    <div className="max-w-5xl">
      <Breadcrumb items={[
        { label: 'Reports', to: '/reports' },
        { label: `${data.report_type} Report` },
      ]} />
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_280px] gap-6 items-start">
        <article className="min-w-0 border border-border rounded-lg bg-surface-1 p-6 md:p-8">
          <div className="flex items-center justify-between gap-4 mb-5 pb-4 border-b border-border">
            <h2 className="font-serif text-xl text-text m-0 capitalize">{data.report_type} Report</h2>
            <span className="text-[11px] font-mono text-text-muted whitespace-nowrap">{formatCreatedAt(data.created_at)}</span>
          </div>
          {data.content ? (
            <MarkdownView content={data.content} headingIds={headings.map((h) => h.id)} />
          ) : (
            <div className="border border-warning/30 rounded-md bg-warning/5 p-5 space-y-2">
              <p className="text-sm text-text m-0 font-medium">Report generated but no content was produced.</p>
              <p className="text-[13px] text-text-secondary m-0 leading-relaxed">
                Possible causes: AI provider not configured (check Settings → Gateway), no records in the selected period, or the LLM returned an empty response.
              </p>
            </div>
          )}
        </article>

        <aside className="hidden lg:flex lg:flex-col gap-4 sticky top-20">
          <div className="border border-border rounded-lg bg-surface-1 p-4 space-y-3">
            <h3 className="text-[11px] font-mono uppercase tracking-wider text-text-muted m-0">Report Info</h3>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-[12px]">
              <dt className="text-text-muted">Type</dt>
              <dd className="text-text m-0 capitalize">{data.report_type}</dd>
              {periodLabel ? (
                <>
                  <dt className="text-text-muted">Period</dt>
                  <dd className="text-text m-0 font-mono">{periodLabel}</dd>
                </>
              ) : null}
              <dt className="text-text-muted">Generated</dt>
              <dd className="text-text m-0 font-mono">{formatCreatedAt(data.created_at)}</dd>
              <dt className="text-text-muted">App</dt>
              <dd className="text-text m-0 font-mono uppercase">{appName}</dd>
            </dl>
          </div>

          {headings.length > 0 ? (
            <nav className="border border-border rounded-lg bg-surface-1 p-4">
              <h3 className="text-[11px] font-mono uppercase tracking-wider text-text-muted m-0 mb-3">Contents</h3>
              <ul className="list-none m-0 p-0 space-y-1.5 max-h-[60vh] overflow-y-auto pr-1">
                {headings.map((h) => (
                  <li
                    key={h.id}
                    style={{ paddingLeft: `${(h.level - 1) * 10}px` }}
                  >
                    <a
                      href={`#${h.id}`}
                      onClick={(e) => {
                        e.preventDefault();
                        const el = document.getElementById(h.id);
                        if (el) {
                          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                          setActiveId(h.id);
                        }
                      }}
                      className={`block text-[12px] leading-snug no-underline truncate transition-colors ${
                        activeId === h.id
                          ? 'text-accent-solo'
                          : 'text-text-secondary hover:text-text'
                      }`}
                      title={h.text}
                    >
                      {h.text}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
