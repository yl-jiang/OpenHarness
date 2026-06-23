import { useState } from 'react';
import { useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { InsightDomain, InsightReportJSON, InsightBlindSpot, InsightItem, InsightPattern, InsightRecommendation, InsightMetric, InsightPeriodComparison } from '../api/types';
import { MarkdownView } from '../components/MarkdownView';
import { useApi } from '../hooks/useApi';

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

const DIRECTION_ARROWS: Record<string, string> = {
  up: '↑',
  down: '↓',
  flat: '→',
};

const STRENGTH_DOTS: Record<string, string> = {
  strong: '●●●',
  moderate: '●●○',
  weak: '●○○',
};

/** CSS-only sparkline using block characters */
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

function HeroBand({ insight, domain, reportType }: { insight: InsightReportJSON; domain: string; reportType: string }) {
  const domainLabel = domain === 'health' ? '健康' : '财务';
  const periodLabel = { weekly: '周报', monthly: '月报', yearly: '年报' }[reportType] || reportType;
  const comparisons = insight.period_comparison || [];

  return (
    <section className="p-5 rounded-lg border border-border bg-surface-1">
      <h2 className="text-lg font-serif text-text mb-2">
        🌱 {domainLabel}{periodLabel}洞察
      </h2>
      {insight.headline && (
        <p className="text-base font-medium text-text mb-2">{insight.headline}</p>
      )}
      {insight.narrative && (
        <p className="text-sm text-text-muted mb-4">{insight.narrative}</p>
      )}
      {comparisons.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {comparisons.map((c: InsightPeriodComparison, i: number) => {
            const arrow = DIRECTION_ARROWS[c.direction] || '';
            const isUp = c.direction === 'up';
            return (
              <span
                key={i}
                className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-mono ${
                  isUp ? 'bg-danger/10 text-danger' : c.direction === 'down' ? 'bg-success/10 text-success' : 'bg-surface-2 text-text-muted'
                }`}
              >
                {c.metric} {c.current}{c.unit || ''} {arrow}{Math.abs(c.delta_pct).toFixed(1)}%
              </span>
            );
          })}
        </div>
      )}
    </section>
  );
}

function BlindSpotsSection({ blindSpots }: { blindSpots: InsightBlindSpot[] }) {
  if (!blindSpots.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">🕳️ 你可能忽视的</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {blindSpots.map((bs, i) => (
          <div key={i} className={`p-4 rounded-lg ${SEVERITY_STYLES[bs.severity] || SEVERITY_STYLES.info}`}>
            <p className="font-medium text-sm mb-1">
              {SEVERITY_ICONS[bs.severity] || 'ℹ️'} {bs.title}
            </p>
            <p className="text-xs opacity-80 mb-1">{bs.why}</p>
            <p className="text-xs opacity-60">证据：{bs.evidence}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function MetricsSection({ metrics }: { metrics: InsightMetric[] }) {
  if (!metrics || !metrics.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">📈 关键指标</h3>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {metrics.map((m, i) => (
          <div key={i} className="p-4 rounded-lg border border-border bg-surface-1">
            <p className="text-xs text-text-muted mb-1">{m.label}</p>
            <p className="text-xl font-mono text-text">
              {m.value}<span className="text-xs text-text-muted ml-1">{m.unit}</span>
            </p>
            {m.trend && m.trend.length > 0 && (
              <div className="mt-2"><Sparkline data={m.trend} /></div>
            )}
            {m.comparison_value != null && (
              <p className="text-xs text-text-muted mt-1">
                vs {m.comparison_label || '上期'} {m.comparison_value}{m.unit}
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function InsightsSection({ insights }: { insights: InsightItem[] }) {
  if (!insights.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">🔍 深度洞察</h3>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {insights.map((ins, i) => (
          <div key={i} className={`p-4 rounded-lg border border-border bg-surface-1 ${SEVERITY_STYLES[ins.severity] || ''}`}>
            <p className="font-medium text-sm mb-2">
              {ins.icon || '🔍'} {ins.title}
            </p>
            <p className="text-xs text-text-muted mb-2">{ins.analysis}</p>
            {ins.evidence && ins.evidence.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {ins.evidence.map((e, j) => (
                  <span key={j} className="px-1.5 py-0.5 rounded text-[10px] bg-surface-2 text-text-muted font-mono">
                    {e}
                  </span>
                ))}
              </div>
            )}
            <div className="flex items-center gap-2">
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                ins.severity === 'alert' ? 'bg-danger/10 text-danger' :
                ins.severity === 'watch' ? 'bg-warning/10 text-warning' :
                'bg-accent-solo/10 text-accent-solo'
              }`}>
                {ins.severity}
              </span>
              {ins.tags && ins.tags.map((t, j) => (
                <span key={j} className="text-[10px] text-text-muted">#{t}</span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function PatternsSection({ patterns }: { patterns: InsightPattern[] }) {
  if (!patterns || !patterns.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">🔗 模式识别</h3>
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

function RecommendationsSection({ recommendations }: { recommendations: InsightRecommendation[] }) {
  if (!recommendations.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-text">💡 行动建议</h3>
      <ol className="space-y-2">
        {recommendations.map((r, i) => (
          <li key={i} className="p-3 rounded-lg border border-border bg-surface-1 text-sm">
            <p className="font-medium text-text">{i + 1}. {r.action}</p>
            <p className="text-xs text-text-muted mt-1">{r.rationale}</p>
            <p className="text-xs text-text-muted">验证信号：{r.expected_signal}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function InsightReportView() {
  const { id } = useParams<{ id: string }>();
  const [showRaw, setShowRaw] = useState(false);

  const { data: report, loading, error } = useApi(
    () => api.insightReports.get(id!),
    [id],
    { enabled: !!id },
  );

  if (loading) {
    return <div className="flex items-center justify-center py-20"><div className="animate-spin w-6 h-6 border-2 border-accent-solo border-t-transparent rounded-full" /></div>;
  }

  if (error || !report) {
    return <div className="text-sm text-danger py-10 text-center">{error || 'Report not found'}</div>;
  }

  const metadata = report.metadata || {};
  const domain = metadata.domain as InsightDomain || 'finance';
  const reportType = report.report_type || 'weekly';
  const insightJson = metadata.insight_json as InsightReportJSON | undefined;

  // Fallback to Markdown if no structured JSON
  if (!insightJson) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 mb-4">
          <a href={`#/insight-reports`} className="text-xs text-text-muted hover:text-text">← 返回</a>
        </div>
        <MarkdownView content={report.content} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <a href={`#/insight-reports`} className="text-xs text-text-muted hover:text-text">← 返回</a>
        <span className="text-xs text-text-muted">
          {report.period_start} ~ {report.period_end}
        </span>
      </div>

      <HeroBand insight={insightJson} domain={domain} reportType={reportType} />
      <BlindSpotsSection blindSpots={insightJson.blind_spots || []} />
      <MetricsSection metrics={insightJson.metrics || []} />
      <InsightsSection insights={insightJson.insights || []} />
      <PatternsSection patterns={insightJson.patterns || []} />
      <RecommendationsSection recommendations={insightJson.recommendations || []} />

      <div className="border-t border-border pt-3">
        <button
          onClick={() => setShowRaw(!showRaw)}
          className="text-xs text-text-muted hover:text-text cursor-pointer"
        >
          {showRaw ? '▶ 收起原始数据' : '▶ 展开原始数据'}
        </button>
        {showRaw && (
          <div className="mt-2 p-4 rounded-lg bg-surface-2 text-xs font-mono text-text-muted overflow-auto max-h-96">
            <MarkdownView content={report.content} />
          </div>
        )}
      </div>
    </div>
  );
}
