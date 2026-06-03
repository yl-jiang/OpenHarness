import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { CountPoint, EmotionPoint, ModelCallDailyPoint, ModelTokenDailyPoint, TagPoint } from '../api/types';

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
  startDate,
  endDate,
}: {
  data: ModelTokenDailyPoint[];
  startDate: string;
  endDate: string;
}) {
  const models = Array.from(new Set(data.map((item) => item.model))).sort();
  const visibleEndDate = endDate || startDate;
  const byDateAndModel = new Map(data.map((item) => [`${item.date}:${item.model}`, item] as const));
  const allDates = buildDateRange(startDate, visibleEndDate);
  const series = models.map((model, index) => ({
    model,
    colors: tokenPalette[index % tokenPalette.length],
  }));

  const chartData = allDates.map((date) => {
    const row: Record<string, number | string | null> = { date };
    models.forEach((model) => {
      const point = byDateAndModel.get(`${date}:${model}`);
      row[inputKey(model)] = point?.input_tokens ?? 0;
      row[outputKey(model)] = point?.output_tokens ?? 0;
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

  return (
    <div className="space-y-3">
      <div className="h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" />
            <XAxis
              dataKey="date"
              stroke="#63636e"
              tick={{ fontSize: 10, fill: '#a1a1aa' }}
              ticks={tickDates}
              tickFormatter={(v: string) => v.slice(8)}
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
              contentStyle={tooltipStyle}
              labelStyle={tooltipLabelStyle}
              itemStyle={tooltipItemStyle}
              formatter={(value: number | string) => {
                const n = Number(value);
                return `${formatTokenAmount(n)} (${formatFullNumber(n)})`;
              }}
              labelFormatter={(value: string) => value}
            />
            {series.flatMap(({ model, colors }) => [
              <Line
                key={inputKey(model)}
                type="linear"
                dataKey={inputKey(model)}
                name={`${model} input`}
                stroke={colors.input}
                strokeWidth={1.75}
                isAnimationActive={false}
                dot={{ r: 2.5, fill: colors.input, strokeWidth: 0 }}
                activeDot={{ r: 4 }}
              />,
              <Line
                key={outputKey(model)}
                type="linear"
                dataKey={outputKey(model)}
                name={`${model} output`}
                stroke={colors.output}
                strokeWidth={1.75}
                strokeDasharray="6 4"
                isAnimationActive={false}
                dot={{ r: 2.5, fill: colors.output, strokeWidth: 0 }}
                activeDot={{ r: 4 }}
              />,
            ])}
          </ComposedChart>
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
                <span className="h-0.5 w-4 rounded-full" style={{ backgroundColor: colors.input }} />
                input
              </span>
              <span className="inline-flex items-center gap-1 text-text-muted">
                <span
                  className="h-0 w-4 border-t border-dashed"
                  style={{ borderColor: colors.output, borderTopWidth: '2px' }}
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
  const byDateAndModel = new Map(data.map((item) => [`${item.date}:${item.model}`, item] as const));
  const allDates = buildDateRange(startDate, visibleEndDate);
  const series = models.map((model, index) => ({
    model,
    color: tokenPalette[index % tokenPalette.length].input,
  }));

  const chartData = allDates.map((date) => {
    const row: Record<string, number | string | null> = { date };
    models.forEach((model) => {
      const point = byDateAndModel.get(`${date}:${model}`);
      row[callCountKey(model)] = point?.count ?? 0;
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

  return (
    <div className="space-y-3">
      <div className="h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" />
            <XAxis
              dataKey="date"
              stroke="#63636e"
              tick={{ fontSize: 10, fill: '#a1a1aa' }}
              ticks={tickDates}
              tickFormatter={(v: string) => v.slice(8)}
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
              contentStyle={tooltipStyle}
              labelStyle={tooltipLabelStyle}
              itemStyle={tooltipItemStyle}
              formatter={(value: number | string) => {
                const n = Number(value);
                return `${formatFullNumber(n)} calls`;
              }}
              labelFormatter={(value: string) => value}
            />
            {series.map(({ model, color }) => (
              <Line
                key={callCountKey(model)}
                type="linear"
                dataKey={callCountKey(model)}
                name={`${model} calls`}
                stroke={color}
                strokeWidth={1.75}
                isAnimationActive={false}
                dot={{ r: 2.5, fill: color, strokeWidth: 0 }}
                activeDot={{ r: 4 }}
              />
            ))}
          </ComposedChart>
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
                <span className="h-0.5 w-4 rounded-full" style={{ backgroundColor: color }} />
                calls
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
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
