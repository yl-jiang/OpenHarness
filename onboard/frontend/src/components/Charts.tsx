import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { CountPoint, EmotionPoint, TagPoint } from '../api/types';

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
