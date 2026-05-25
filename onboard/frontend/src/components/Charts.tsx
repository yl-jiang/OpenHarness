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

const colors = ['#6c5ce7', '#00b894', '#74b9ff', '#fdcb6e', '#e17055', '#fd79a8'];

export function DailyLineChart({ data }: { data: CountPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data}>
        <CartesianGrid stroke="rgba(255,255,255,0.08)" />
        <XAxis dataKey="date" stroke="#8888a0" />
        <YAxis stroke="#8888a0" allowDecimals={false} />
        <Tooltip contentStyle={{ background: '#12121a', border: '1px solid #333' }} />
        <Line type="monotone" dataKey="count" stroke="#74b9ff" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function EmotionPieChart({ data }: { data: EmotionPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <PieChart>
        <Pie data={data} dataKey="count" nameKey="emotion" outerRadius={84} label>
          {data.map((item, index) => (
            <Cell key={item.emotion} fill={colors[index % colors.length]} />
          ))}
        </Pie>
        <Tooltip contentStyle={{ background: '#12121a', border: '1px solid #333' }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

export function TagBarChart({ data }: { data: TagPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data.slice(0, 12)}>
        <CartesianGrid stroke="rgba(255,255,255,0.08)" />
        <XAxis dataKey="tag" stroke="#8888a0" />
        <YAxis stroke="#8888a0" allowDecimals={false} />
        <Tooltip contentStyle={{ background: '#12121a', border: '1px solid #333' }} />
        <Bar dataKey="count" fill="#00b894" radius={[8, 8, 0, 0]} />
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
  return (
    <div className="heatmap">
      {days.map((day) => (
        <span
          key={day.date}
          title={`${day.date}: ${day.count}`}
          className={`heat heat-${Math.min(day.count, 4)}`}
        />
      ))}
    </div>
  );
}
