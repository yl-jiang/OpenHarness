import { useCallback, useEffect, useState } from 'react';

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { CountPoint, EmotionPoint, ModelCallDailyPoint, ModelTokenDailyPoint, TagPoint } from '../api/types';

const palette = ['#b8956a', '#6a9e8e', '#8b7db8', '#c4a35a', '#b87070', '#6a8a9e'];

const tooltipStyle = {
  background: '#1c1c21',
  border: '1px solid #2e2e33',
  borderRadius: '6px',
  fontSize: '12px',
  fontFamily: 'var(--font-mono)',
  color: '#e4e4e7',
};

const tooltipLabelStyle = { color: '#a1a1aa' };
const tooltipItemStyle = { color: '#e4e4e7' };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function DataAwareCursor(props: any) {
  const { points, offset, height } = props;
  if (!points?.length) return null;
  const hasData = points.some((p: any) => p.y != null);
  if (!hasData) return null;
  const x = points[0]?.x;
  if (x == null) return null;
  const top = offset?.top ?? 0;
  const chartHeight = height ?? 200;
  return <line x1={x} y1={top} x2={x} y2={top + chartHeight} stroke="#3f3f46" strokeDasharray="3 3" />;
}

export const tokenPalette = [
  { input: '#60a5fa', output: '#93c5fd' },
  { input: '#f59e0b', output: '#fcd34d' },
  { input: '#34d399', output: '#86efac' },
  { input: '#f472b6', output: '#f9a8d4' },
  { input: '#a78bfa', output: '#c4b5fd' },
  { input: '#fb7185', output: '#fda4af' },
];

// lodash.get (used by recharts) treats dots as path separators — sanitize model names.
function sanitizeModelKey(model: string): string {
  return model.replace(/[^a-zA-Z0-9_]/g, '_');
}

function inputKey(model: string): string {
  return `in_${sanitizeModelKey(model)}`;
}

function outputKey(model: string): string {
  return `out_${sanitizeModelKey(model)}`;
}

function callCountKey(model: string): string {
  return `count_${sanitizeModelKey(model)}`;
}

function formatFullNumber(value: number): string {
  return value.toLocaleString();
}

export function formatTokenAmount(value: number): string {
  const abs = Math.abs(value);
  const compact = (divisor: number, suffix: string): string => {
    const formatted = (value / divisor).toFixed(abs >= divisor * 100 ? 0 : 1).replace(/\.0$/, '');
    return `${formatted}${suffix}`;
  };
  if (abs >= 1e12) return compact(1e12, 'T');
  if (abs >= 1e9) return compact(1e9, 'B');
  if (abs >= 1e6) return compact(1e6, 'M');
  if (abs >= 1e3) return compact(1e3, 'K');
  return formatFullNumber(value);
}

function formatLocalDateKey(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function todayStr(): string {
  return formatLocalDateKey(new Date());
}

function buildDateRange(startDate: string, endDate: string): string[] {
  if (!startDate || !endDate || startDate > endDate) {
    return [];
  }
  const days: string[] = [];
  // Parse as local midnight to avoid UTC-offset cross-day shifts
  const [sy, sm, sd] = startDate.split('-').map(Number);
  const [ey, em, ed] = endDate.split('-').map(Number);
  const cursor = new Date(sy, sm - 1, sd);
  const last = new Date(ey, em - 1, ed);
  while (cursor <= last) {
    days.push(formatLocalDateKey(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return days;
}

function renderTokenDot(props: {
  cx?: number;
  cy?: number;
  value?: number | string | null;
  fill?: string;
  stroke?: string;
}) {
  const value = props.value == null ? 0 : typeof props.value === 'number' ? props.value : Number(props.value);
  if (props.cx == null || props.cy == null || value <= 0) {
    return <circle cx={props.cx ?? 0} cy={props.cy ?? 0} r={0} fill="transparent" />;
  }
  return <circle cx={props.cx} cy={props.cy} r={2.5} fill={props.fill ?? props.stroke ?? '#e4e4e7'} />;
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function useOneShotLineAnimation(duration = 900): boolean {
  const [isAnimationActive, setIsAnimationActive] = useState(() => !prefersReducedMotion());

  useEffect(() => {
    if (!isAnimationActive || typeof window === 'undefined') {
      return;
    }
    const timer = window.setTimeout(() => {
      setIsAnimationActive(false);
    }, duration);
    return () => window.clearTimeout(timer);
  }, [duration, isAnimationActive]);

  return isAnimationActive;
}

export function DailyLineChart({ data }: { data: CountPoint[] }) {
  const isAnimationActive = useOneShotLineAnimation();

  const tickStep = data.length > 20 ? Math.ceil(data.length / 6) : data.length > 10 ? 3 : 1;
  const ticks = data
    .filter((_, i) => i === 0 || i === data.length - 1 || i % tickStep === 0)
    .map((d) => d.date);

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -8 }}>
        <defs>
          <linearGradient id="dailyAreaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#d4a574" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#d4a574" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" />
        <XAxis
          dataKey="date"
          stroke="#63636e"
          tick={{ fontSize: 10, fill: '#a1a1aa' }}
          ticks={ticks}
          tickFormatter={(v: string) => {
            const d = new Date(v + 'T00:00:00');
            return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
          }}
          tickLine={{ stroke: '#2e2e33' }}
        />
        <YAxis
          stroke="#63636e"
          tick={{ fontSize: 10, fill: '#a1a1aa' }}
          allowDecimals={false}
          width={32}
          tickLine={{ stroke: '#2e2e33' }}
          axisLine={{ stroke: '#2e2e33' }}
        />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle} />
        <Area
          type="monotone"
          dataKey="count"
          stroke="#d4a574"
          strokeWidth={2}
          fill="url(#dailyAreaGrad)"
          dot={{ r: 2, fill: '#d4a574', strokeWidth: 0 }}
          activeDot={{ r: 4, fill: '#d4a574', stroke: '#1c1c21', strokeWidth: 2 }}
          isAnimationActive={isAnimationActive}
          animationDuration={900}
          animationEasing="ease-out"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function EmotionPieChart({ data }: { data: EmotionPoint[] }) {
  if (!data.length) {
    return <div className="text-[12px] text-text-muted py-4 text-center">No data yet</div>;
  }
  const total = data.reduce((sum, d) => sum + d.count, 0);
  return (
    <div className="relative flex-1 min-h-0">
      <div className="absolute inset-0 bottom-12">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="count" nameKey="emotion" outerRadius="85%" innerRadius="48%" strokeWidth={0}>
              {data.map((item, index) => (
                <Cell key={item.emotion} fill={palette[index % palette.length]} />
              ))}
            </Pie>
            <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="absolute bottom-0 left-0 right-0 flex flex-wrap justify-center gap-x-5 gap-y-2 py-1">
        {data.map((item, index) => (
          <div key={item.emotion} className="flex items-center gap-1.5 text-[12px]">
            <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: palette[index % palette.length] }} />
            <span className="text-text-secondary">{item.emotion}</span>
            <span className="text-text-muted tabular-nums">
              {total > 0 ? Math.round((item.count / total) * 100) : 0}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function EmotionBarList({ data }: { data: EmotionPoint[] }) {
  if (!data.length) {
    return <div className="text-[12px] text-text-muted py-4 text-center">No data yet</div>;
  }

  const sorted = [...data].sort((a, b) => b.count - a.count);
  const total = sorted.reduce((s, d) => s + d.count, 0);
  const maxCount = sorted[0]?.count ?? 1;

  return (
    <div className="space-y-2">
      {sorted.map((item, i) => {
        const pct = total > 0 ? (item.count / total) * 100 : 0;
        const barWidth = maxCount > 0 ? (item.count / maxCount) * 100 : 0;
        const opacity = Math.max(0.3, 1 - i * 0.12);
        return (
          <div key={item.emotion} className="flex items-center gap-3">
            <span className="text-[12px] text-text w-16 flex-shrink-0 truncate">{item.emotion}</span>
            <div className="flex-1 h-[6px] rounded-full bg-surface-2 overflow-hidden">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${barWidth}%`,
                  backgroundColor: 'var(--color-accent-solo)',
                  opacity,
                  minWidth: item.count > 0 ? 3 : 0,
                }}
              />
            </div>
            <span className="text-[11px] font-mono text-text-muted w-12 text-right tabular-nums flex-shrink-0">
              {item.count} <span className="text-text-muted/60">{pct.toFixed(0)}%</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function TagBarChart({ data }: { data: TagPoint[] }) {
  const sorted = [...data].sort((a, b) => b.count - a.count).slice(0, 30);
  const chartH = sorted.length * 28 + 24;
  const [activeTag, setActiveTag] = useState<string | null>(null);

  const handleMouseMove = useCallback((state: any) => {
    if (state?.activeLabel != null) {
      setActiveTag(String(state.activeLabel));
    }
  }, []);

  const handleMouseLeave = useCallback(() => {
    setActiveTag(null);
  }, []);

  const renderTick = useCallback(
    (props: any) => {
      const { x, y, payload } = props;
      const isActive = payload?.value === activeTag;
      return (
        <text
          x={x}
          y={y}
          dy={4}
          textAnchor="end"
          fill={isActive ? '#e4e4e7' : '#63636e'}
          fontWeight={isActive ? 600 : 400}
          fontSize={11}
        >
          {payload.value}
        </text>
      );
    },
    [activeTag],
  );

  return (
    <div className="overflow-y-auto pr-1" style={{ maxHeight: 380 }}>
      <ResponsiveContainer width="100%" height={Math.max(chartH, 120)}>
        <BarChart
          data={sorted}
          layout="vertical"
          margin={{ top: 0, right: 8, bottom: 0, left: 0 }}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
        >
          <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" horizontal={false} />
          <XAxis type="number" stroke="#63636e" tick={{ fontSize: 11 }} allowDecimals={false} />
          <YAxis type="category" dataKey="tag" stroke="#63636e" tick={renderTick} width={90} />
          <Tooltip cursor={false} contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle} />
          <Bar dataKey="count" fill={palette[0]} radius={[0, 3, 3, 0]} barSize={18} activeBar={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ModelTokenUsageChart({
  data,
  startDate,
  endDate,
}: {
  data: ModelTokenDailyPoint[];
  startDate: string;
  endDate: string;
}) {
  const models = Array.from(new Set(data.map((item) => item.model))).sort();
  const visibleEndDate = endDate || startDate;
  const today = todayStr();
  const byDateAndModel = new Map(data.map((item) => [`${item.date}:${item.model}`, item] as const));
  const allDates = buildDateRange(startDate, visibleEndDate);
  const series = models.map((model, index) => ({
    model,
    colors: tokenPalette[index % tokenPalette.length],
  }));

  const chartData = allDates.map((date) => {
    const row: Record<string, number | string | null> = { date };
    const isFuture = date > today;
    models.forEach((model) => {
      const point = byDateAndModel.get(`${date}:${model}`);
      row[inputKey(model)] = isFuture ? null : (point?.input_tokens ?? 0);
      row[outputKey(model)] = isFuture ? null : (point?.output_tokens ?? 0);
    });
    return row;
  });

  // Show day-of-month numbers: 1st, every 5th, and the last visible day.
  const lastDay = visibleEndDate ? parseInt(visibleEndDate.slice(8), 10) : 0;
  const tickDates = allDates.filter((d) => {
    const day = parseInt(d.slice(8), 10);
    return day === 1 || day % 5 === 0 || day === lastDay;
  });

  const allValues = chartData.flatMap((row) =>
    models.flatMap((m) => [row[inputKey(m)] as number, row[outputKey(m)] as number])
  );
  const dataMax = Math.max(0, ...allValues);
  const yMax = dataMax <= 0 ? 10 : Math.ceil(dataMax * 1.1);
  const isAnimationActive = useOneShotLineAnimation();

  return (
    <div className="space-y-3">
      <div className="h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
            <defs>
              {series.flatMap(({ model, colors }) => {
                const key = sanitizeModelKey(model);
                return [
                  <linearGradient key={`gi_${key}`} id={`tokenIn_${key}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={colors.input} stopOpacity={0.28} />
                    <stop offset="100%" stopColor={colors.input} stopOpacity={0.02} />
                  </linearGradient>,
                  <linearGradient key={`go_${key}`} id={`tokenOut_${key}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={colors.output} stopOpacity={0.22} />
                    <stop offset="100%" stopColor={colors.output} stopOpacity={0.02} />
                  </linearGradient>,
                ];
              })}
            </defs>
            <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" />
            <XAxis
              dataKey="date"
              stroke="#63636e"
              tick={{ fontSize: 10, fill: '#a1a1aa' }}
              ticks={tickDates}
              tickFormatter={(v: string) => `${v.slice(5, 7)}/${v.slice(8)}`}
              tickLine={{ stroke: '#2e2e33' }}
            />
            <YAxis
              orientation="left"
              domain={[0, yMax]}
              tick={{ fontSize: 11, fill: '#a1a1aa' }}
              tickLine={{ stroke: '#2e2e33' }}
              axisLine={{ stroke: '#2e2e33' }}
              stroke="#63636e"
              tickFormatter={formatTokenAmount}
              allowDecimals={false}
              width={64}
              label={{ value: 'Tokens', angle: -90, position: 'insideLeft', offset: 12, fill: '#63636e', fontSize: 10 }}
            />
            <Tooltip
              cursor={<DataAwareCursor />}
              content={(props) => (
                <FilteredTooltip
                  {...props}
                  formatValue={(n) => `${formatTokenAmount(n)} (${formatFullNumber(n)})`}
                />
              )}
            />
            {series.flatMap(({ model, colors }) => {
              const key = sanitizeModelKey(model);
              return [
                <Area
                  key={inputKey(model)}
                  type="linear"
                  dataKey={inputKey(model)}
                  name={`${model} input`}
                  stroke={colors.input}
                  strokeWidth={1.75}
                  fill={`url(#tokenIn_${key})`}
                  baseValue={0}
                  isAnimationActive={isAnimationActive}
                  animationDuration={900}
                  animationEasing="ease-out"
                  dot={{ r: 2.5, fill: colors.input, strokeWidth: 0 }}
                  activeDot={{ r: 4 }}
                />,
                <Area
                  key={outputKey(model)}
                  type="linear"
                  dataKey={outputKey(model)}
                  name={`${model} output`}
                  stroke={colors.output}
                  strokeWidth={1.75}
                  strokeDasharray="6 4"
                  fill={`url(#tokenOut_${key})`}
                  baseValue={0}
                  isAnimationActive={isAnimationActive}
                  animationDuration={900}
                  animationEasing="ease-out"
                  dot={{ r: 2.5, fill: colors.output, strokeWidth: 0 }}
                  activeDot={{ r: 4 }}
                />,
              ];
            })}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {series.length > 0 && (
        <div className="flex flex-wrap gap-2 text-[11px] font-mono text-text-secondary">
          {series.map(({ model, colors }) => (
            <div
              key={model}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-surface-2 px-2.5 py-1.5"
            >
              <span className="text-text">{model}</span>
              <span className="inline-flex items-center gap-1 text-text-muted">
                <span
                  className="h-2 w-4 rounded-sm"
                  style={{ backgroundColor: colors.input, opacity: 0.35, boxShadow: `inset 0 0 0 1px ${colors.input}` }}
                />
                input
              </span>
              <span className="inline-flex items-center gap-1 text-text-muted">
                <span
                  className="h-2 w-4 rounded-sm border border-dashed"
                  style={{ borderColor: colors.output }}
                />
                output
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ModelCallUsageChart({
  data,
  startDate,
  endDate,
}: {
  data: ModelCallDailyPoint[];
  startDate: string;
  endDate: string;
}) {
  const models = Array.from(new Set(data.map((item) => item.model))).sort();
  const visibleEndDate = endDate || startDate;
  const today = todayStr();
  const byDateAndModel = new Map(data.map((item) => [`${item.date}:${item.model}`, item] as const));
  const allDates = buildDateRange(startDate, visibleEndDate);
  const series = models.map((model, index) => ({
    model,
    color: tokenPalette[index % tokenPalette.length].input,
  }));

  const chartData = allDates.map((date) => {
    const row: Record<string, number | string | null> = { date };
    const isFuture = date > today;
    models.forEach((model) => {
      const point = byDateAndModel.get(`${date}:${model}`);
      row[callCountKey(model)] = isFuture ? null : (point?.count ?? 0);
    });
    return row;
  });

  // Show day-of-month numbers: 1st, every 5th, and the last visible day.
  const lastDay = visibleEndDate ? parseInt(visibleEndDate.slice(8), 10) : 0;
  const tickDates = allDates.filter((d) => {
    const day = parseInt(d.slice(8), 10);
    return day === 1 || day % 5 === 0 || day === lastDay;
  });

  const allValues = chartData.flatMap((row) => models.map((m) => row[callCountKey(m)] as number));
  const dataMax = Math.max(0, ...allValues);
  const yMax = dataMax <= 0 ? 10 : Math.ceil(dataMax * 1.1);
  const isAnimationActive = useOneShotLineAnimation();

  return (
    <div className="space-y-3">
      <div className="h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
            <defs>
              {series.map(({ model, color }) => {
                const key = sanitizeModelKey(model);
                return (
                  <linearGradient key={key} id={`call_${key}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={color} stopOpacity={0.28} />
                    <stop offset="100%" stopColor={color} stopOpacity={0.02} />
                  </linearGradient>
                );
              })}
            </defs>
            <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" />
            <XAxis
              dataKey="date"
              stroke="#63636e"
              tick={{ fontSize: 10, fill: '#a1a1aa' }}
              ticks={tickDates}
              tickFormatter={(v: string) => `${v.slice(5, 7)}/${v.slice(8)}`}
              tickLine={{ stroke: '#2e2e33' }}
            />
            <YAxis
              orientation="left"
              domain={[0, yMax]}
              tick={{ fontSize: 11, fill: '#a1a1aa' }}
              tickLine={{ stroke: '#2e2e33' }}
              axisLine={{ stroke: '#2e2e33' }}
              stroke="#63636e"
              tickFormatter={formatFullNumber}
              allowDecimals={false}
              width={64}
              label={{ value: 'Calls', angle: -90, position: 'insideLeft', offset: 12, fill: '#63636e', fontSize: 10 }}
            />
            <Tooltip
              cursor={<DataAwareCursor />}
              content={(props) => (
                <FilteredTooltip
                  {...props}
                  formatValue={(n) => `${formatFullNumber(n)} calls`}
                />
              )}
            />
            {series.map(({ model, color }) => {
              const key = sanitizeModelKey(model);
              return (
                <Area
                  key={callCountKey(model)}
                  type="linear"
                  dataKey={callCountKey(model)}
                  name={`${model} calls`}
                  stroke={color}
                  strokeWidth={1.75}
                  fill={`url(#call_${key})`}
                  baseValue={0}
                  isAnimationActive={isAnimationActive}
                  animationDuration={900}
                  animationEasing="ease-out"
                  dot={{ r: 2.5, fill: color, strokeWidth: 0 }}
                  activeDot={{ r: 4 }}
                />
              );
            })}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {series.length > 0 && (
        <div className="flex flex-wrap gap-2 text-[11px] font-mono text-text-secondary">
          {series.map(({ model, color }) => (
            <div
              key={model}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-surface-2 px-2.5 py-1.5"
            >
              <span className="text-text">{model}</span>
              <span className="inline-flex items-center gap-1 text-text-muted">
                <span
                  className="h-2 w-4 rounded-sm"
                  style={{ backgroundColor: color, opacity: 0.35, boxShadow: `inset 0 0 0 1px ${color}` }}
                />
                calls
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FilteredTooltip({
  active,
  payload,
  label,
  formatValue,
}: {
  active?: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload?: ReadonlyArray<{ name?: string | number; value?: unknown; color?: string }>;
  label?: string | number;
  formatValue: (value: number) => string;
}) {
  if (!active || !payload?.length) return null;
  const visible = payload.filter((entry) => Number(entry.value) > 0);
  if (visible.length === 0) return null;
  return (
    <div style={{ ...tooltipStyle, padding: '8px 12px' }}>
      <div style={{ ...tooltipLabelStyle, marginBottom: 4 }}>{label}</div>
      {visible.map((entry) => (
        <div key={String(entry.name)} style={{ display: 'flex', alignItems: 'center', gap: 6, lineHeight: 1.6 }}>
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', backgroundColor: entry.color ?? '#888' }} />
          <span style={tooltipItemStyle}>{entry.name}</span>
          <span style={{ ...tooltipItemStyle, marginLeft: 'auto', paddingLeft: 12 }}>{formatValue(Number(entry.value))}</span>
        </div>
      ))}
    </div>
  );
}

export function ActivityHeatmap({ data }: { data: CountPoint[] }) {
  const now = new Date();
  const [viewYear, setViewYear] = useState(now.getFullYear());
  const [viewMonth, setViewMonth] = useState(now.getMonth());

  const byDate = new Map(data.map((item) => [item.date, item.count]));
  const todayKey = formatLocalDateKey(now);

  const firstDow = (new Date(viewYear, viewMonth, 1).getDay() + 6) % 7;
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();

  const cells: (number | null)[] = [];
  for (let i = 0; i < firstDow; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);

  const monthLabel = new Date(viewYear, viewMonth).toLocaleString('en-US', { month: 'long', year: 'numeric' });
  const isCurrentMonth = viewYear === now.getFullYear() && viewMonth === now.getMonth();

  const navigate = (delta: number) => {
    let m = viewMonth + delta;
    let y = viewYear;
    if (m < 0) { m = 11; y--; } else if (m > 11) { m = 0; y++; }
    setViewMonth(m);
    setViewYear(y);
  };

  const dotCount = (count: number): number => {
    if (count <= 0) return 0;
    if (count <= 2) return 1;
    if (count <= 5) return 2;
    return 3;
  };

  const weekdayLabels = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

  return (
    <div className="select-none">
      <div className="flex items-center justify-between mb-3 px-0.5">
        <div className="flex items-center gap-1">
          <button
            onClick={() => navigate(-1)}
            className="w-7 h-7 rounded flex items-center justify-center text-text-muted hover:bg-surface-2 hover:text-text transition-colors text-sm"
          >
            ‹
          </button>
          <button
            onClick={() => navigate(1)}
            className="w-7 h-7 rounded flex items-center justify-center text-text-muted hover:bg-surface-2 hover:text-text transition-colors text-sm"
          >
            ›
          </button>
        </div>
        <span className="text-[13px] font-medium text-text">{monthLabel}</span>
        {isCurrentMonth ? (
          <span className="w-10" />
        ) : (
          <button
            onClick={() => { setViewYear(now.getFullYear()); setViewMonth(now.getMonth()); }}
            className="text-[11px] text-accent-solo hover:text-text transition-colors w-10 text-right"
          >
            today
          </button>
        )}
      </div>
      <div className="grid grid-cols-7 text-center">
        {weekdayLabels.map((label, i) => (
          <div key={i} className="text-[11px] text-text-muted py-1.5 font-medium">{label}</div>
        ))}
        {cells.map((day, i) => {
          if (day === null) return <div key={`e${i}`} />;
          const dateKey = `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
          const count = byDate.get(dateKey) ?? 0;
          const isToday = dateKey === todayKey;
          const dots = dotCount(count);
          return (
            <div
              key={`d${day}`}
              className="flex flex-col items-center py-1"
              title={`${dateKey}: ${count} ${count === 1 ? 'entry' : 'entries'}`}
            >
              <span
                className={`w-[clamp(28px,4vw,42px)] h-[clamp(28px,4vw,42px)] flex items-center justify-center rounded-full text-[13px] leading-none
                  ${isToday ? 'bg-red-500 text-white font-semibold' : 'text-text'}`}
              >
                {day}
              </span>
              <div className="flex gap-[3px] h-[6px] mt-1">
                {Array.from({ length: dots }).map((_, j) => (
                  <span key={j} className="w-[4px] h-[4px] rounded-full bg-accent-solo" />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
