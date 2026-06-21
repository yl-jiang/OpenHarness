import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid,
  Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

import { api } from '../api/client';
import type {
  FitnessDay, HealthOverview, HealthTimelineItem,
  SleepDay, SoloHealthRecord,
} from '../api/types';
import { StatsCard } from '../components/StatsCard';
import { SubjectFilter } from '../components/health/SubjectFilter';
import { HealthTimeline } from '../components/health/HealthTimeline';
import { useApi } from '../hooks/useApi';

const palette = ['#b8956a', '#6a9e8e', '#8b7db8', '#c4a35a', '#b87070', '#6a8a9e', '#7eb87e', '#c48a6a'];

const tooltipStyle = {
  background: '#1c1c21',
  border: '1px solid #2e2e33',
  borderRadius: '6px',
  fontSize: '12px',
  fontFamily: 'var(--font-mono)',
  color: '#e4e4e7',
};

const CATEGORY_LABELS: Record<string, string> = {
  medical: '就诊', symptom: '症状', medication: '用药', fitness: '运动',
  sleep: '睡眠', nutrition: '饮食', mental: '心理', vital: '体征',
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="p-5 rounded-lg border border-border bg-surface-1">
      <h3 className="text-sm font-medium text-text mb-4">{title}</h3>
      {children}
    </section>
  );
}

function EmptyState() {
  return <div className="text-sm text-text-muted py-8 text-center">暂无数据</div>;
}

export function Health() {
  const [selectedSubject, setSelectedSubject] = useState<string | null>(null);

  const { data: subjectsData } = useApi(
    () => api.health.subjects(),
    [],
  );

  const { data: overview } = useApi(
    () => api.health.overview(selectedSubject ?? undefined),
    [selectedSubject],
  );

  const currentMonth = new Date().toISOString().slice(0, 7);
  const [chartMonth, setChartMonth] = useState(currentMonth);

  const { data: fitnessData } = useApi(
    () => api.health.fitness(selectedSubject ?? undefined, 30, chartMonth),
    [selectedSubject, chartMonth],
  );

  const { data: sleepData } = useApi(
    () => api.health.sleep(selectedSubject ?? undefined, 30, chartMonth),
    [selectedSubject, chartMonth],
  );

  const { data: symptomData } = useApi(
    () => api.health.symptoms(selectedSubject ?? undefined, 90),
    [selectedSubject],
  );

  const { data: medData } = useApi(
    () => api.health.medications(selectedSubject ?? undefined, 90),
    [selectedSubject],
  );

  const { data: mentalData } = useApi(
    () => api.health.mental(selectedSubject ?? undefined, 30),
    [selectedSubject],
  );

  const { data: vitalsData } = useApi(
    () => api.health.vitals(selectedSubject ?? undefined, 90),
    [selectedSubject],
  );

  const { data: vitalTrends } = useApi(
    () => api.health.vitalTrends(selectedSubject ?? undefined, chartMonth),
    [selectedSubject, chartMonth],
  );

  const TIMELINE_PAGE_SIZE = 20;
  const [timelinePage, setTimelinePage] = useState(1);
  const [timelineItems, setTimelineItems] = useState<HealthTimelineItem[]>([]);
  const [timelineTotal, setTimelineTotal] = useState(0);
  const [timelineExpanded, setTimelineExpanded] = useState(false);

  useEffect(() => {
    if (!timelineExpanded) return;
    let cancelled = false;
    const offset = (timelinePage - 1) * TIMELINE_PAGE_SIZE;
    api.health.timeline(selectedSubject ?? undefined, { limit: TIMELINE_PAGE_SIZE, offset })
      .then((res) => {
        if (!cancelled) {
          setTimelineItems(res.items);
          setTimelineTotal(res.total);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [selectedSubject, timelineExpanded, timelinePage]);

  // Reset timeline page when subject changes
  useEffect(() => {
    setTimelinePage(1);
  }, [selectedSubject]);

  const subjects = subjectsData?.subjects ?? {};

  // Compute tick dates for charts (show 1st, every 5th, last day — same as Dashboard)
  const tickDates = useCallback((dates: string[]) => {
    if (dates.length === 0) return [];
    const lastDay = parseInt(dates[dates.length - 1].slice(8), 10);
    return dates.filter((d) => {
      const day = parseInt(d.slice(8), 10);
      return day === 1 || day % 5 === 0 || day === lastDay;
    });
  }, []);

  const fitnessTicks = useMemo(() => tickDates(fitnessData?.daily?.map((d) => d.date) ?? []), [fitnessData, tickDates]);
  const sleepTicks = useMemo(() => tickDates(sleepData?.daily?.map((d) => d.date) ?? []), [sleepData, tickDates]);
  const vitalTrendTicks = useMemo(() => tickDates(vitalTrends?.daily?.map((d) => d.date) ?? []), [vitalTrends, tickDates]);

  // ── Apple Health import ──
  const [importing, setImporting] = useState(false);
  const [importDialog, setImportDialog] = useState<{ open: boolean; title: string; message: string; success?: boolean }>({
    open: false, title: '', message: '',
  });
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleImport = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true);
    setImportDialog({ open: true, title: '导入中', message: `正在解析 ${file.name}（${(file.size / 1024 / 1024).toFixed(1)} MB）...` });
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch('/api/solo/health/import/apple-health', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (data.ok) {
        const typeLabels: Record<string, string> = {
          vital: '体征', fitness: '运动', sleep: '睡眠', mental: '心理',
        };
        const byType = data.by_type
          ? Object.entries(data.by_type as Record<string, number>)
              .map(([k, v]) => `${typeLabels[k] || k} ${v}`)
              .join('、')
          : '';
        const detail = [
          data.message,
          data.date_range ? `日期范围：${data.date_range}` : '',
          byType ? `分类明细：${byType}` : '',
        ].filter(Boolean).join('\n');
        setImportDialog({ open: true, title: '导入完成', message: detail, success: true });
        if (data.inserted > 0) {
          setTimeout(() => window.location.reload(), 1500);
        }
      } else {
        setImportDialog({ open: true, title: '导入失败', message: data.error || '未知错误', success: false });
      }
    } catch (err) {
      setImportDialog({ open: true, title: '导入失败', message: err instanceof Error ? err.message : '网络错误', success: false });
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, []);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-serif text-text">Health</h1>
          <p className="text-sm text-text-muted mt-1">身心健康统计与趋势</p>
        </div>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip,.xml"
            className="hidden"
            onChange={handleImport}
          />
          <button
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-2 text-text-secondary hover:bg-surface-3 transition-colors cursor-pointer border border-border disabled:opacity-50"
            disabled={importing}
            onClick={() => fileInputRef.current?.click()}
          >
            {importing ? '导入中...' : '导入 Apple Health'}
          </button>
        </div>
      </div>

      {/* Subject Filter */}
      <SubjectFilter subjects={subjects} selected={selectedSubject} onSelect={setSelectedSubject} />

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatsCard label="总记录" value={overview?.total_records ?? 0} icon="♡" />
        <StatsCard label="本周运动" value={overview?.fitness_count_7d ?? 0} hint="次" icon="🏃" />
        <StatsCard label="平均睡眠" value={overview?.avg_sleep_hours_30d ? `${overview.avg_sleep_hours_30d}h` : '—'} icon="😴" />
        <StatsCard label="活跃用药" value={overview?.active_medications ?? 0} icon="💊" />
      </div>

      {/* Category Breakdown */}
      {overview && Object.keys(overview.by_category).length > 0 && (
        <Section title="类别分布">
          <div className="flex flex-wrap gap-3">
            {Object.entries(overview.by_category).map(([cat, count], i) => (
              <div key={cat} className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: palette[i % palette.length] }} />
                <span className="text-xs text-text-secondary">
                  {CATEGORY_LABELS[cat] ?? cat} ({count})
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Monthly trend charts - shared month picker */}
      <div className="flex items-center justify-end mb-2">
        <input
          type="month"
          value={chartMonth}
          onChange={(e) => setChartMonth(e.target.value)}
          className="text-xs bg-surface-2 border border-border rounded px-2 py-1 text-text-secondary font-mono"
        />
      </div>

      {/* Fitness + Sleep */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Section title="运动趋势">
          {fitnessData?.daily?.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={fitnessData.daily}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2e2e33" />
                <XAxis dataKey="date" ticks={fitnessTicks} tick={{ fontSize: 10, fill: '#a1a1aa' }} tickFormatter={(v: string) => `${v.slice(5, 7)}/${v.slice(8)}`} tickLine={{ stroke: '#2e2e33' }} />
                <YAxis tick={{ fontSize: 10, fill: '#a1a1aa' }} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'transparent' }} />
                <Bar dataKey="total_minutes" fill="#6a9e8e" radius={[2, 2, 0, 0]} name="分钟" activeBar={false} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyState />}
        </Section>

        <Section title="睡眠趋势">
          {sleepData?.daily?.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={sleepData.daily}>
                <defs>
                  <linearGradient id="sleepGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#8b7db8" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#8b7db8" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="2 4" stroke="#1f1f24" />
                <XAxis dataKey="date" ticks={sleepTicks} tick={{ fontSize: 10, fill: '#a1a1aa' }} tickFormatter={(v: string) => `${v.slice(5, 7)}/${v.slice(8)}`} tickLine={{ stroke: '#2e2e33' }} />
                <YAxis tick={{ fontSize: 10, fill: '#a1a1aa' }} domain={[0, 'auto']} width={36} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: '#3f3f46', strokeDasharray: '3 3' }} />
                <Area type="linear" dataKey="hours" stroke="#8b7db8" fill="url(#sleepGrad)" strokeWidth={1.75} name="小时" connectNulls dot={{ r: 2, fill: '#8b7db8' }} activeDot={{ r: 4, fill: '#8b7db8' }} />
              </AreaChart>
            </ResponsiveContainer>
          ) : <EmptyState />}
        </Section>
      </div>

      {/* Vital Trends: Heart Rate + Blood Oxygen */}
      <div className="p-5 rounded-lg border border-border bg-surface-1">
        <h3 className="text-sm font-medium text-text mb-4">心率 & 血氧趋势</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Heart Rate Chart */}
          <div>
            <div className="flex items-center gap-3 mb-2">
              <span className="text-xs text-text-muted">心率 (bpm)</span>
              <div className="flex items-center gap-2 text-[10px]">
                <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-[#f472b6] inline-block" />最高</span>
                <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-[#fda4af] inline-block border-dashed border-t border-[#fda4af]" />最低</span>
              </div>
            </div>
            {vitalTrends?.daily?.some((d) => d.hr_max != null) ? (
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={vitalTrends.daily}>
                  <defs>
                    <linearGradient id="hrMax" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#f472b6" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="#f472b6" stopOpacity={0.02} />
                    </linearGradient>
                    <linearGradient id="hrMin" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#fda4af" stopOpacity={0.2} />
                      <stop offset="100%" stopColor="#fda4af" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="2 4" stroke="#1f1f24" />
                  <XAxis dataKey="date" ticks={vitalTrendTicks} tick={{ fontSize: 10, fill: '#a1a1aa' }} tickFormatter={(v: string) => `${v.slice(5, 7)}/${v.slice(8)}`} tickLine={{ stroke: '#2e2e33' }} />
                  <YAxis tick={{ fontSize: 10, fill: '#a1a1aa' }} domain={['auto', 'auto']} width={36} />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: '#3f3f46', strokeDasharray: '3 3' }} />
                  <Area type="linear" dataKey="hr_max" stroke="#f472b6" fill="url(#hrMax)" strokeWidth={1.75} dot={{ r: 2 }} activeDot={{ r: 4 }} name="最高心率" connectNulls />
                  <Area type="linear" dataKey="hr_min" stroke="#fda4af" fill="url(#hrMin)" strokeWidth={1.75} strokeDasharray="6 4" dot={{ r: 2 }} activeDot={{ r: 4 }} name="最低心率" connectNulls />
                </AreaChart>
              </ResponsiveContainer>
            ) : <EmptyState />}
          </div>

          {/* Blood Oxygen Chart */}
          <div>
            <div className="flex items-center gap-3 mb-2">
              <span className="text-xs text-text-muted">血氧 (%)</span>
              <div className="flex items-center gap-2 text-[10px]">
                <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-[#60a5fa] inline-block" />最高</span>
                <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-[#93c5fd] inline-block border-dashed border-t border-[#93c5fd]" />最低</span>
              </div>
            </div>
            {vitalTrends?.daily?.some((d) => d.spo2_max != null) ? (
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={vitalTrends.daily}>
                  <defs>
                    <linearGradient id="spo2Max" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#60a5fa" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="#60a5fa" stopOpacity={0.02} />
                    </linearGradient>
                    <linearGradient id="spo2Min" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#93c5fd" stopOpacity={0.2} />
                      <stop offset="100%" stopColor="#93c5fd" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="2 4" stroke="#1f1f24" />
                  <XAxis dataKey="date" ticks={vitalTrendTicks} tick={{ fontSize: 10, fill: '#a1a1aa' }} tickFormatter={(v: string) => `${v.slice(5, 7)}/${v.slice(8)}`} tickLine={{ stroke: '#2e2e33' }} />
                  <YAxis tick={{ fontSize: 10, fill: '#a1a1aa' }} domain={['auto', 'auto']} width={36} />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: '#3f3f46', strokeDasharray: '3 3' }} />
                  <Area type="linear" dataKey="spo2_max" stroke="#60a5fa" fill="url(#spo2Max)" strokeWidth={1.75} dot={{ r: 2 }} activeDot={{ r: 4 }} name="最高血氧" connectNulls />
                  <Area type="linear" dataKey="spo2_min" stroke="#93c5fd" fill="url(#spo2Min)" strokeWidth={1.75} strokeDasharray="6 4" dot={{ r: 2 }} activeDot={{ r: 4 }} name="最低血氧" connectNulls />
                </AreaChart>
              </ResponsiveContainer>
            ) : <EmptyState />}
          </div>
        </div>
      </div>

      {/* Vital Signs - full width, scrollable */}
      <Section title="体征数据">
        {vitalsData?.daily?.length ? (
          <div className="group">
            <div className="scrollbar-hover overflow-y-auto pr-1" style={{ maxHeight: 360 }}>
              <div className="space-y-3">
                {vitalsData.daily.map((entry) => {
                  const m = entry.metrics;
                  const chips: { label: string; value: string; color?: string }[] = [];
                  if (m.heart_rate_avg) chips.push({ label: '心率', value: `${m.heart_rate_avg} bpm`, color: 'text-pink-400' });
                  if (m.resting_heart_rate) chips.push({ label: '静息', value: `${m.resting_heart_rate} bpm`, color: 'text-pink-300' });
                  if (m.spo2_avg) chips.push({ label: '血氧', value: `${m.spo2_avg}%`, color: 'text-blue-400' });
                  if (m.hrv_avg) chips.push({ label: 'HRV', value: `${m.hrv_avg} ms`, color: 'text-purple-400' });
                  if (m.vo2_max) chips.push({ label: 'VO₂max', value: `${m.vo2_max}`, color: 'text-green-400' });
                  if (m.weight_kg) chips.push({ label: '体重', value: `${m.weight_kg} kg`, color: 'text-yellow-400' });
                  if (m.wrist_temperature) chips.push({ label: '腕温', value: `${m.wrist_temperature}°`, color: 'text-orange-400' });
                  if (m.respiratory_rate) chips.push({ label: '呼吸率', value: `${m.respiratory_rate}/min` });
                  if (m.noise_db_avg) chips.push({ label: '噪音', value: `${m.noise_db_avg} dB` });
                  return (
                    <div key={entry.date} className="py-2 border-b border-border/30 last:border-0">
                      <div className="font-mono text-[11px] text-text-muted mb-1.5">{entry.date}</div>
                      <div className="flex flex-wrap gap-x-4 gap-y-1">
                        {chips.map((c) => (
                          <span key={c.label} className="text-xs">
                            <span className="text-text-muted">{c.label} </span>
                            <span className={`font-medium ${c.color || 'text-text'}`}>{c.value}</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        ) : <EmptyState />}
      </Section>

      {/* Symptoms + Medications */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Section title="症状追踪">
          {symptomData?.by_item?.length ? (
            <div className="space-y-2">
              {symptomData.by_item.slice(0, 8).map((item) => (
                <div key={item.item} className="flex items-center justify-between text-sm">
                  <span className="text-text">{item.item}</span>
                  <span className="text-text-muted font-mono text-xs">{item.count}次</span>
                </div>
              ))}
            </div>
          ) : <EmptyState />}
        </Section>

        <Section title="用药记录">
          {medData?.usage?.length ? (
            <div className="space-y-2">
              {medData.usage.slice(0, 8).map((item) => (
                <div key={item.name} className="flex items-center justify-between text-sm">
                  <span className="text-text">{item.name}</span>
                  <span className="text-text-muted font-mono text-xs">{item.count}次</span>
                </div>
              ))}
              {medData.active.length > 0 && (
                <div className="mt-3 pt-3 border-t border-border">
                  <div className="text-xs text-text-muted mb-1">当前用药</div>
                  {medData.active.slice(0, 5).map((r) => (
                    <div key={r.id} className="text-xs text-text-secondary">
                      {r.medication_name || r.item} {r.dosage && `(${r.dosage})`} {r.frequency && r.frequency}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : <EmptyState />}
        </Section>
      </div>

      {/* Mental Health */}
      <Section title="心理状态">
        {mentalData?.daily?.length ? (
          <div className="space-y-4">
            {(Object.keys(mentalData.mood_distribution).length > 0 || Object.keys(mentalData.stress_distribution).length > 0) && (
              <div className="flex flex-wrap gap-6">
                {Object.keys(mentalData.mood_distribution).length > 0 && (
                  <div>
                    <div className="text-xs text-text-muted mb-2">情绪分布</div>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(mentalData.mood_distribution)
                        .sort((a, b) => b[1] - a[1])
                        .map(([mood, count]) => {
                          const total = Object.values(mentalData.mood_distribution).reduce((s: number, v) => s + (v as number), 0);
                          const pct = total > 0 ? Math.round(((count as number) / total) * 100) : 0;
                          return (
                            <span key={mood} className="text-xs px-2.5 py-1 rounded-md bg-surface-2 border border-border text-text-secondary">
                              {mood} <span className="text-text-muted">{pct}%</span>
                            </span>
                          );
                        })}
                    </div>
                  </div>
                )}
                {Object.keys(mentalData.stress_distribution).length > 0 && (
                  <div>
                    <div className="text-xs text-text-muted mb-2">压力水平</div>
                    <div className="flex gap-2">
                      {(['low', 'moderate', 'high'] as const).map((level) => {
                        const count = (mentalData.stress_distribution as Record<string, number>)[level] || 0;
                        if (count === 0) return null;
                        return (
                          <span key={level} className={`text-xs px-2.5 py-1 rounded-md border ${
                            level === 'high' ? 'bg-red-900/20 border-red-800/30 text-red-300' :
                            level === 'moderate' ? 'bg-yellow-900/20 border-yellow-800/30 text-yellow-300' :
                            'bg-green-900/20 border-green-800/30 text-green-300'
                          }`}>
                            {level === 'low' ? '低' : level === 'moderate' ? '中' : '高'} {count}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
            <div className="group">
              <div className="scrollbar-hover overflow-y-auto pr-1" style={{ maxHeight: 240 }}>
                <div className="space-y-2">
                  {mentalData.daily.map((entry) => (
                    <div key={entry.date} className="flex items-start gap-3 py-1.5">
                      <span className="font-mono text-[11px] text-text-muted mt-0.5 flex-shrink-0">{entry.date}</span>
                      <div className="flex flex-wrap gap-2 items-center">
                        {entry.mood && (
                          <span className="text-xs px-2 py-0.5 rounded bg-surface-2 text-text-secondary">{entry.mood}</span>
                        )}
                        {entry.stress && (
                          <span className={`text-xs px-2 py-0.5 rounded ${
                            entry.stress === 'high' ? 'bg-red-900/20 text-red-300' :
                            entry.stress === 'moderate' ? 'bg-yellow-900/20 text-yellow-300' :
                            'bg-green-900/20 text-green-300'
                          }`}>
                            {entry.stress === 'low' ? '低压' : entry.stress === 'moderate' ? '中压' : '高压'}
                          </span>
                        )}
                        {entry.description && (
                          <span className="text-xs text-text-muted truncate max-w-xs">{entry.description}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ) : <EmptyState />}
      </Section>

      {/* Timeline - collapsible */}
      <div className="rounded-lg border border-border bg-surface-1">
        <button
          className="w-full flex items-center justify-between p-5 cursor-pointer bg-transparent border-0 text-left"
          onClick={() => setTimelineExpanded((v) => !v)}
        >
          <h3 className="text-sm font-medium text-text m-0">健康时间线</h3>
          <span className={`text-text-muted text-xs transition-transform duration-200 ${timelineExpanded ? 'rotate-180' : ''}`}>▾</span>
        </button>
        {timelineExpanded && (
          <div className="px-5 pb-5 border-t border-border/30">
            <HealthTimeline
              items={timelineItems}
              total={timelineTotal}
              page={timelinePage}
              pageSize={TIMELINE_PAGE_SIZE}
              onPageChange={(p) => setTimelinePage(p)}
            />
          </div>
        )}
      </div>

      {/* Import Result Dialog */}
      {importDialog.open && (
        <div className="fixed inset-0 z-[90] grid place-items-center bg-black/50" onClick={() => setImportDialog({ open: false, title: '', message: '' })}>
          <div
            className="bg-surface-1 border border-border rounded-lg p-6 max-w-md w-full mx-4 animate-[fade-in_0.15s_ease-out_both]"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="flex items-center gap-2 mb-3">
              {importing ? (
                <span className="text-yellow-400 animate-pulse text-lg">⟳</span>
              ) : importDialog.success ? (
                <span className="text-green-400 text-lg">✓</span>
              ) : (
                <span className="text-red-400 text-lg">✕</span>
              )}
              <h3 className="text-sm font-medium text-text m-0">{importDialog.title}</h3>
            </div>
            <p className="text-[13px] text-text-secondary m-0 leading-relaxed whitespace-pre-line">{importDialog.message}</p>
            {!importing && (
              <div className="flex justify-end mt-4">
                <button
                  onClick={() => setImportDialog({ open: false, title: '', message: '' })}
                  className="text-[12px] px-3 py-1.5 rounded-md border border-border bg-surface-2 text-text-secondary hover:text-text cursor-pointer transition-colors"
                >
                  关闭
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
