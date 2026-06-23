import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { InsightDomain, Report } from '../api/types';
import { useApi, LIVE_REFRESH_INTERVAL_MS } from '../hooks/useApi';
import { useInsightGenerating } from '../hooks/useInsightGenerating';

interface InsightReportListProps {
  domain: InsightDomain;
}

const DOMAIN_LABELS: Record<InsightDomain, string> = {
  health: '健康',
  finance: '财务',
};

const REPORT_TYPE_OPTIONS = [
  { label: '周报', value: 'weekly' },
  { label: '月报', value: 'monthly' },
  { label: '年报', value: 'yearly' },
];

export function InsightReportList({ domain }: InsightReportListProps) {
  const [generating, setGenerating] = useInsightGenerating(domain);
  const [showTypePicker, setShowTypePicker] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!showTypePicker) return;
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowTypePicker(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showTypePicker]);
  const [genError, setGenError] = useState<string | null>(null);

  const { data: reports, loading, reload } = useApi(
    () => api.insightReports.list({ domain }),
    [domain],
    { refreshIntervalMs: generating ? LIVE_REFRESH_INTERVAL_MS : undefined },
  );

  const handleGenerate = async (reportType: string) => {
    setShowTypePicker(false);
    setGenerating(reportType);
    setGenError(null);
    try {
      await api.insightReports.generate(domain, reportType);
      reload();
    } catch (err) {
      setGenError(err instanceof Error ? err.message : '生成失败');
    } finally {
      setGenerating(null);
    }
  };

  const recentReports = (reports || []).slice(0, 5);

  return (
    <section className="p-4 rounded-lg border border-border bg-surface-1">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-text">
          ✨ {DOMAIN_LABELS[domain]}洞察报告
        </h3>
        <div className="relative" ref={pickerRef}>
          <button
            onClick={() => setShowTypePicker(!showTypePicker)}
            disabled={!!generating}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent-solo/10 text-accent-solo hover:bg-accent-solo/20 transition-colors cursor-pointer border border-accent-solo/30 disabled:opacity-50"
          >
            {generating ? '生成中...' : '✨ 生成洞察报告'}
          </button>
          {showTypePicker && (
            <div className="absolute right-0 top-full mt-1 z-10 bg-surface-2 border border-border rounded-lg shadow-lg overflow-hidden">
              {REPORT_TYPE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => handleGenerate(opt.value)}
                  className="block w-full px-4 py-2 text-xs text-text hover:bg-surface-3 transition-colors cursor-pointer text-left"
                >
                  {opt.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {genError && (
        <p className="text-xs text-danger mb-2">{genError}</p>
      )}

      {loading ? (
        <div className="text-xs text-text-muted py-2">加载中...</div>
      ) : recentReports.length === 0 ? (
        <p className="text-xs text-text-muted py-2">暂无洞察报告，点击上方按钮生成</p>
      ) : (
        <div className="space-y-1.5">
          {recentReports.map((r: Report) => (
            <Link
              key={r.id}
              to={`/reports/${r.id}`}
              className="flex items-center justify-between px-2 py-1.5 rounded hover:bg-surface-2 transition-colors group"
            >
              <span className="text-xs text-text group-hover:text-accent-solo transition-colors">
                {r.report_type === 'weekly' ? '周报' : r.report_type === 'monthly' ? '月报' : '年报'}
                {r.period_start && ` · ${r.period_start}`}
              </span>
              <span className="text-[10px] text-text-muted">
                {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}
              </span>
            </Link>
          ))}
          {reports && reports.length > 5 && (
            <Link
              to={`/reports`}
              className="block text-xs text-accent-solo hover:underline py-1"
            >
              查看全部 ({reports.length})
            </Link>
          )}
        </div>
      )}
    </section>
  );
}
