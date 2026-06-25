import { useMemo } from 'react';

import { useApi } from '../../hooks/useApi';
import { api } from '../../api/client';
import type { PeriodAnalysis } from '../../api/types';

interface PeriodTrackerProps {
  subject: string;
}

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];

function EmptyState() {
  return (
    <div className="text-sm text-text-muted py-8 text-center">
      暂无生理期记录。让 solo 记录一次（例如"老婆今天来大姨妈了"）就会在这里看到周期追踪。
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[11px] text-text-muted font-mono">{label}</span>
      <span className="text-sm text-text font-medium">
        {value}
        {hint && <span className="text-text-muted text-[11px] ml-0.5">{hint}</span>}
      </span>
    </div>
  );
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = iso.slice(5, 7);
  const d = iso.slice(8, 10);
  return `${m}/${d}`;
}

function buildMonthGrid(year: number, monthZeroBased: number): { date: string; day: number }[] {
  const first = new Date(year, monthZeroBased, 1);
  const daysInMonth = new Date(year, monthZeroBased + 1, 0).getDate();
  const leadingBlank = (first.getDay() + 6) % 7;
  const cells: { date: string; day: number }[] = [];
  for (let i = 0; i < leadingBlank; i++) {
    cells.push({ date: '', day: 0 });
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const date = `${year}-${String(monthZeroBased + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    cells.push({ date, day: d });
  }
  return cells;
}

function MonthBlock({
  year, month, dayMap, ovulationDate,
}: {
  year: number;
  month: number;
  dayMap: Map<string, { state: 'period' | 'predicted' | 'ovulation' | 'fertile'; flow: string }>;
  ovulationDate: string | null;
}) {
  const cells = buildMonthGrid(year, month);
  const monthLabel = `${year} 年 ${month + 1} 月`;
  const todayStr = new Date().toISOString().slice(0, 10);

  return (
    <div className="min-w-0">
      <div className="text-[12px] text-text font-medium mb-1.5 font-mono">{monthLabel}</div>
      <div className="grid grid-cols-7 gap-0.5">
        {WEEKDAYS.map((w) => (
          <div key={w} className="text-[10px] text-text-muted text-center py-0.5">{w}</div>
        ))}
        {cells.map((cell, idx) => {
          if (cell.day === 0) {
            return <div key={`blank-${idx}`} className="aspect-square" />;
          }
          const info = dayMap.get(cell.date);
          const isOvulation = ovulationDate === cell.date;
          const isToday = cell.date === todayStr;
          let bg = 'bg-surface-2';
          let ring = '';
          let text = 'text-text-secondary';
          if (info?.state === 'period') {
            bg = 'bg-red-500/70';
            text = 'text-white';
          } else if (info?.state === 'predicted') {
            bg = 'bg-red-500/25';
            ring = 'ring-1 ring-red-500/40';
            text = 'text-red-100';
          } else if (isOvulation) {
            bg = 'bg-amber-400/80';
            text = 'text-white';
          } else if (info?.state === 'fertile') {
            bg = 'bg-amber-400/30';
            text = 'text-amber-100';
          }
          if (isToday) {
            ring = 'ring-2 ring-blue-400';
            text = text.replace('text-text-secondary', 'text-text');
          }
          return (
            <div
              key={cell.date}
              className={`aspect-square grid place-items-center text-[11px] rounded-sm ${bg} ${ring} ${text}`}
              title={info ? `${cell.date} · ${info.state}${info.flow ? ` · ${info.flow}` : ''}` : cell.date}
            >
              {cell.day}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function PeriodTracker({ subject }: PeriodTrackerProps) {
  const { data } = useApi(
    () => api.health.period(subject, 365),
    [subject],
  );

  const analysis: PeriodAnalysis | undefined = data ?? undefined;

  const dayMap = useMemo(() => {
    const m = new Map<string, { state: 'period' | 'predicted' | 'ovulation' | 'fertile'; flow: string }>();
    if (!analysis?.calendar) return m;
    for (const d of analysis.calendar) {
      m.set(d.date, { state: d.state, flow: d.flow });
    }
    if (analysis.ovulation_estimate) {
      const existing = m.get(analysis.ovulation_estimate);
      if (!existing) {
        m.set(analysis.ovulation_estimate, { state: 'ovulation', flow: '' });
      }
    }
    return m;
  }, [analysis]);

  if (!analysis || analysis.total === 0) {
    return <EmptyState />;
  }

  const { stats, forecast, ovulation_estimate, fertile_window } = analysis;

  const today = new Date();
  const months: { year: number; month: number }[] = [];
  for (let offset = -1; offset <= 1; offset++) {
    const d = new Date(today.getFullYear(), today.getMonth() + offset, 1);
    months.push({ year: d.getFullYear(), month: d.getMonth() });
  }

  const nextStartDays = forecast?.next_start
    ? Math.round(
        (new Date(forecast.next_start).getTime() - today.getTime()) / (1000 * 60 * 60 * 24),
      )
    : null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="上次开始" value={fmtDate(stats.last_start)} />
        <Stat
          label="平均周期"
          value={stats.avg_cycle_days ?? '—'}
          hint={stats.avg_cycle_days ? '天' : ''}
        />
        <Stat
          label="平均经期"
          value={stats.avg_period_days ?? '—'}
          hint={stats.avg_period_days ? '天' : ''}
        />
        <Stat
          label="预计下次"
          value={forecast ? fmtDate(forecast.next_start) : '—'}
          hint={nextStartDays != null && forecast ? `(${nextStartDays > 0 ? `${nextStartDays}天后` : nextStartDays === 0 ? '今天' : `${-nextStartDays}天前`})` : ''}
        />
      </div>

      {(ovulation_estimate || fertile_window?.start || forecast) && (
        <div className="flex flex-wrap gap-3 text-[11px] font-mono">
          {ovulation_estimate && (
            <span className="inline-flex items-center gap-1.5 text-text-secondary">
              <span className="w-2 h-2 rounded-full bg-amber-400 inline-block" />
              排卵日 {fmtDate(ovulation_estimate)}
            </span>
          )}
          {fertile_window?.start && fertile_window?.end && (
            <span className="inline-flex items-center gap-1.5 text-text-secondary">
              <span className="w-2 h-2 rounded-full bg-amber-400/30 ring-1 ring-amber-400/50 inline-block" />
              易孕期 {fmtDate(fertile_window.start)}–{fmtDate(fertile_window.end)}
            </span>
          )}
          {forecast && (
            <span className="inline-flex items-center gap-1.5 text-text-secondary">
              <span className="w-2 h-2 rounded-full bg-red-500/40 ring-1 ring-red-500/60 inline-block" />
              预计经期 {fmtDate(forecast.next_start)}–{fmtDate(forecast.next_end)}
            </span>
          )}
          <span className="inline-flex items-center gap-1.5 text-text-secondary">
            <span className="w-2 h-2 rounded-full bg-red-500/70 inline-block" />
            已记录
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {months.map((m) => (
          <MonthBlock
            key={`${m.year}-${m.month}`}
            year={m.year}
            month={m.month}
            dayMap={dayMap}
            ovulationDate={ovulation_estimate}
          />
        ))}
      </div>

      {analysis.cycles.length > 0 && (
        <div>
          <div className="text-[11px] text-text-muted font-mono mb-1.5">最近周期</div>
          <div className="space-y-1">
            {analysis.cycles
              .slice()
              .reverse()
              .slice(0, 6)
              .map((c) => (
                <div key={c.start_date} className="flex items-center justify-between text-xs py-1 border-b border-border/30 last:border-0">
                  <span className="text-text font-mono">
                    {fmtDate(c.start_date)} – {fmtDate(c.end_date)}
                  </span>
                  <span className="text-text-muted">
                    {c.period_days}天{c.length_days ? ` · 周期 ${c.length_days}天` : ''}
                    {c.flow_summary ? ` · ${c.flow_summary}` : ''}
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
