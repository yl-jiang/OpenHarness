import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { ActivityHeatmap, EmotionBarList, ModelCallUsageChart, ModelTokenUsageChart, formatTokenAmount } from '../components/Charts';
import { StatsCard } from '../components/StatsCard';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

// --- Date card helpers ---

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** Approximate solar term dates (month, day, name). Stable year-to-year (±1 day). */
const SOLAR_TERMS: [number, number, string][] = [
  [1, 5, 'Minor Cold'], [1, 20, 'Major Cold'],
  [2, 4, 'Start of Spring'], [2, 19, 'Rain Water'],
  [3, 5, 'Awakening of Insects'], [3, 20, 'Spring Equinox'],
  [4, 4, 'Clear and Bright'], [4, 20, 'Grain Rain'],
  [5, 5, 'Start of Summer'], [5, 21, 'Lesser Fullness'],
  [6, 5, 'Grain in Ear'], [6, 21, 'Summer Solstice'],
  [7, 7, 'Lesser Heat'], [7, 22, 'Greater Heat'],
  [8, 7, 'Start of Autumn'], [8, 23, 'End of Heat'],
  [9, 7, 'White Dew'], [9, 23, 'Autumnal Equinox'],
  [10, 8, 'Cold Dew'], [10, 23, 'Frost\'s Descent'],
  [11, 7, 'Start of Winter'], [11, 22, 'Minor Snow'],
  [12, 7, 'Major Snow'], [12, 22, 'Winter Solstice'],
];

/**
 * Fixed-date special occasions (Gregorian calendar).
 * Solar terms are appended at runtime from SOLAR_TERMS.
 */
const FIXED_OCCASIONS: Record<string, string> = {
  '1-1': 'New Year\'s Day',
  '2-14': 'Valentine\'s Day',
  '3-8': 'International Women\'s Day',
  '4-1': 'April Fools\' Day',
  '5-1': 'Labour Day',
  '5-4': 'Youth Day',
  '6-1': 'Children\'s Day',
  '7-1': 'CPC Founding Day',
  '8-1': 'Army Day',
  '9-10': 'Teachers\' Day',
  '10-1': 'National Day',
  '12-25': 'Christmas',
  '12-31': 'New Year\'s Eve',
};
// Auto-add solar terms (same key format)
for (const [m, d, name] of SOLAR_TERMS) FIXED_OCCASIONS[`${m}-${d}`] = name;

function DateCard() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const key = `${now.getMonth() + 1}-${now.getDate()}`;
  const occasion = FIXED_OCCASIONS[key] ?? null;
  const time = now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
  return (
    <article className="p-5 rounded-lg border border-border bg-surface-1 hover:bg-surface-2 transition-colors group sm:col-span-2">
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-1.5">
          <span className="text-3xl font-serif text-text tabular-nums leading-none">{now.getDate()}</span>
          <span className="text-sm font-medium text-text">{MONTHS[now.getMonth()]}</span>
          <span className="text-[12px] font-mono text-text-muted">{now.getFullYear()}</span>
        </div>
        <span className="text-xl font-mono text-text tabular-nums">{time}</span>
      </div>
      <div className="mt-1.5 text-[12px] uppercase tracking-wider text-text-muted">
        {WEEKDAYS[now.getDay()]}
        {occasion ? <span className="normal-case ml-2 text-accent-solo">{occasion}</span> : null}
      </div>
    </article>
  );
}

export function Dashboard({ appName }: { appName: AppName }) {
  const { data, error, loading } = useApi(() => api.stats(appName), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Failed to load dashboard.'}</div>;
  }

  return (
    <div className="space-y-6">
      {/* Section label */}
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-2xl text-text m-0">Overview</h2>
        <span className="text-[11px] font-mono text-text-muted uppercase tracking-wider">{appName} · today</span>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatsCard label="Entries" value={data.total_entries} />
        <StatsCard label="Records" value={data.total_records} />
        <StatsCard label="This week" value={data.this_week_records} />
        <StatsCard label="Pending todos" value={data.pending_todos} />
        <StatsCard label="Model calls" value={data.llm_total_calls} />
        <StatsCard label="Vision calls" value={data.vision_total_calls} />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <article className="p-5 rounded-lg border border-border bg-surface-1 hover:bg-surface-2 transition-colors group sm:col-span-2">
          <div className="text-sm font-medium text-text truncate" title={data.current_model}>
            {data.current_model || '—'}
          </div>
          <div className="mt-1.5 text-[12px] uppercase tracking-wider text-text-muted font-medium">Current LLM model</div>
        </article>
        {data.vision_model ? (
          <article className="p-5 rounded-lg border border-border bg-surface-1 hover:bg-surface-2 transition-colors group sm:col-span-2">
            <div className="text-sm font-medium text-text truncate" title={data.vision_model}>
              {data.vision_model}
            </div>
            <div className="mt-1.5 text-[12px] uppercase tracking-wider text-text-muted font-medium">Current vision model</div>
          </article>
        ) : null}
        <DateCard />
      </div>

      <section className="p-5 border border-border rounded-lg bg-surface-1">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h3 className="text-sm font-medium text-text m-0">Token Usage</h3>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-text-muted">
              <span>
                Daily totals · current month view
              </span>
              <span>focus · {data.llm_daily_focus_date}</span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-success animate-[pulse-dot_1.5s_ease-in-out_infinite]" />
                live · 5s
              </span>
            </div>
          </div>
          <div className="flex flex-wrap justify-end gap-2 text-[12px] font-mono">
            <span
              title={`${data.llm_daily_input_tokens.toLocaleString()} input tokens on ${data.llm_daily_focus_date}`}
              className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-text"
            >
              <span className="text-text-muted">input</span>
              <span className="tabular-nums">{formatTokenAmount(data.llm_daily_input_tokens)}</span>
            </span>
            <span
              title={`${data.llm_daily_output_tokens.toLocaleString()} output tokens on ${data.llm_daily_focus_date}`}
              className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-text"
            >
              <span className="text-text-muted">output</span>
              <span className="tabular-nums">{formatTokenAmount(data.llm_daily_output_tokens)}</span>
            </span>
          </div>
        </div>
        <ModelTokenUsageChart
          data={data.llm_monthly_tokens}
          startDate={data.llm_monthly_start_date}
          endDate={data.llm_monthly_end_date}
        />
      </section>

      <section className="p-5 border border-border rounded-lg bg-surface-1">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h3 className="text-sm font-medium text-text m-0">Model Usage</h3>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-text-muted">
              <span>
                Daily totals · current month view
              </span>
              <span>focus · {data.llm_daily_focus_date}</span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-success animate-[pulse-dot_1.5s_ease-in-out_infinite]" />
                live · 5s
              </span>
            </div>
          </div>
          <div className="flex flex-wrap justify-end gap-2 text-[12px] font-mono">
            <span
              title={`${data.llm_daily_total_calls.toLocaleString()} calls on ${data.llm_daily_focus_date}`}
              className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-text"
            >
              <span className="text-text-muted">calls</span>
              <span className="tabular-nums">{data.llm_daily_total_calls.toLocaleString()}</span>
            </span>
          </div>
        </div>
        <ModelCallUsageChart
          data={data.llm_monthly_model_calls}
          startDate={data.llm_monthly_start_date}
          endDate={data.llm_monthly_end_date}
        />
      </section>

      {/* Charts grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="p-5 border border-border rounded-lg bg-surface-1">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-text m-0">Activity</h3>
            <Link to="/records" className="text-[12px] text-text-muted hover:text-text no-underline transition-colors">View all →</Link>
          </div>
          <ActivityHeatmap data={data.daily_counts} />
        </section>
        <section className="p-5 border border-border rounded-lg bg-surface-1">
          <h3 className="text-sm font-medium text-text m-0 mb-4">Emotions</h3>
          <EmotionBarList data={data.emotion_distribution} />
        </section>
      </div>

      {/* Wolo extras */}
      {appName === 'wolo' ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <StatsCard label="Decisions" value={data.total_decisions ?? 0} />
          <StatsCard label="Highlights" value={data.total_highlights ?? 0} />
          <StatsCard label="Open blockers" value={data.open_blockers ?? 0} />
        </div>
      ) : null}
    </div>
  );
}
