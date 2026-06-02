import { Fragment } from 'react';

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { CountPoint, EmotionPoint, ModelTokenDailyPoint, TagPoint } from '../api/types';

const palette = ['#d4a574', '#5eead4', '#a78bfa', '#fbbf24', '#f87171', '#34d399'];

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
const tokenPalette = [
  { input: '#60a5fa', output: '#93c5fd' },
  { input: '#f59e0b', output: '#fcd34d' },
  { input: '#34d399', output: '#86efac' },
  { input: '#f472b6', output: '#f9a8d4' },
  { input: '#a78bfa', output: '#c4b5fd' },
  { input: '#fb7185', output: '#fda4af' },
];

// lodash.get (used by recharts) treats dots as path separators — sanitize model names.
function sanitizeModelKey(model: string): string {
  return model.replace(/[^a-zA-Z0-9_\-]/g, '_');
}

function inputKey(model: string): string {
  return `in_${sanitizeModelKey(model)}`;
}

function outputKey(model: string): string {
  return `out_${sanitizeModelKey(model)}`;
}

function formatTokens(value: number): string {
  if (value >= 1e12) return `${(value / 1e12).toFixed(1)}T`;
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return String(value);
}

function getCurrentMonthRange(): { start: string; end: string } {
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth();
  const pad = (n: number) => String(n).padStart(2, '0');
  const start = `${year}-${pad(month + 1)}-01`;
  const lastDay = new Date(year, month + 1, 0).getDate();
  const end = `${year}-${pad(month + 1)}-${pad(lastDay)}`;
  return { start, end };
}

function buildDateRange(startDate: string, endDate: string): string[] {
  if (!startDate || !endDate || startDate > endDate) {
    return [];
  }
  const days: string[] = [];
  const cursor = new Date(`${startDate}T00:00:00Z`);
  const last = new Date(`${endDate}T00:00:00Z`);
  while (cursor <= last) {
    days.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
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

export function DailyLineChart({ data }: { data: CountPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data}>
        <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" />
        <XAxis dataKey="date" stroke="#63636e" tick={{ fontSize: 11 }} />
        <YAxis stroke="#63636e" tick={{ fontSize: 11 }} allowDecimals={false} width={30} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle} />
        <Line type="monotone" dataKey="count" stroke="#d4a574" strokeWidth={1.5} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function EmotionPieChart({ data }: { data: EmotionPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <PieChart>
        <Pie data={data} dataKey="count" nameKey="emotion" outerRadius={72} innerRadius={40} strokeWidth={0}>
          {data.map((item, index) => (
            <Cell key={item.emotion} fill={palette[index % palette.length]} />
          ))}
        </Pie>
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle} />
      </PieChart>
    </ResponsiveContainer>
  );
}

export function TagBarChart({ data }: { data: TagPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data.slice(0, 12)}>
        <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" />
        <XAxis dataKey="tag" stroke="#63636e" tick={{ fontSize: 11 }} />
        <YAxis stroke="#63636e" tick={{ fontSize: 11 }} allowDecimals={false} width={30} />
        <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle} />
        <Bar dataKey="count" fill="#5eead4" radius={[3, 3, 0, 0]} barSize={20} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function ModelTokenUsageChart({
  data,
}: {
  data: ModelTokenDailyPoint[];
  startDate: string;
  endDate: string;
}) {
  const models = Array.from(new Set(data.map((item) => item.model))).sort();

  if (models.length === 0) {
    return (
      <div className="h-[280px] flex items-center justify-center text-sm text-text-muted">
        No token usage this month.
      </div>
    );
  }

  // Always span the full current month so future dates are visible.
  const { start: monthStart, end: monthEnd } = getCurrentMonthRange();
  const today = new Date().toISOString().slice(0, 10);

  const byDateAndModel = new Map(data.map((item) => [`${item.date}:${item.model}`, item] as const));

  const allDates = buildDateRange(monthStart, monthEnd);

  // Build one row per date. Future dates get null so the line stops at today.
  const chartData = allDates.map((date) => {
    const row: Record<string, number | string | null> = { date };
    models.forEach((model) => {
      const point = byDateAndModel.get(`${date}:${model}`);
      const isFuture = date > today;
      row[inputKey(model)] = isFuture ? null : (point?.input_tokens ?? 0);
      row[outputKey(model)] = isFuture ? null : (point?.output_tokens ?? 0);
    });
    return row;
  });

  // Show 1st, every 5th, and last day of month as X-axis labels.
  const lastDay = parseInt(monthEnd.slice(8), 10);
  const tickDates = allDates.filter((d) => {
    const day = parseInt(d.slice(8), 10);
    return day === 1 || day % 5 === 0 || day === lastDay;
  });

  const chartKey = data.map((d) => `${d.date}:${d.model}:${d.input_tokens}:${d.output_tokens}`).join('|');

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart key={chartKey} data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" />
        <XAxis
          dataKey="date"
          stroke="#63636e"
          tick={{ fontSize: 10 }}
          ticks={tickDates}
          tickFormatter={(v: string) => v.slice(8)}
        />
        <YAxis
          stroke="#63636e"
          tick={{ fontSize: 11 }}
          width={52}
          tickFormatter={formatTokens}
          allowDecimals={false}
        />
        <Tooltip
          contentStyle={tooltipStyle}
          labelStyle={tooltipLabelStyle}
          itemStyle={tooltipItemStyle}
          formatter={(value: number | string) => formatTokens(Number(value))}
          labelFormatter={(value: string) => value}
        />
        <Legend wrapperStyle={{ fontSize: '11px', color: '#a1a1aa', paddingTop: '6px' }} />
        {models.map((model, index) => {
          const colors = tokenPalette[index % tokenPalette.length];
          return (
            <Fragment key={model}>
              <Line
                type="monotone"
                dataKey={inputKey(model)}
                name={`${model} in`}
                stroke={colors.input}
                strokeWidth={1.5}
                isAnimationActive={false}
                dot={renderTokenDot}
                activeDot={{ r: 4 }}
                connectNulls={false}
              />
              <Line
                type="monotone"
                dataKey={outputKey(model)}
                name={`${model} out`}
                stroke={colors.output}
                strokeWidth={1.5}
                strokeDasharray="5 4"
                isAnimationActive={false}
                dot={renderTokenDot}
                activeDot={{ r: 4 }}
                connectNulls={false}
              />
            </Fragment>
          );
        })}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function ActivityHeatmap({ data }: { data: CountPoint[] }) {
  const byDate = new Map(data.map((item) => [item.date, item.count]));
  const today = new Date();
  const days = Array.from({ length: 91 }, (_, index) => {
    const date = new Date(today);
    date.setDate(today.getDate() - (90 - index));
    const key = date.toISOString().slice(0, 10);
    return { date: key, count: byDate.get(key) ?? 0 };
  });

  const levels = ['bg-surface-2', 'bg-accent-solo/30', 'bg-accent-solo/50', 'bg-accent-solo/70', 'bg-accent-solo'];

  return (
    <div className="grid grid-cols-13 gap-[3px]">
      {days.map((day) => (
        <span
          key={day.date}
          title={`${day.date}: ${day.count}`}
          className={`aspect-square rounded-[3px] ${levels[Math.min(day.count, 4)]}`}
        />
      ))}
    </div>
  );
}
