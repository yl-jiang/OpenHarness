import { useMemo } from 'react';
import {
  ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Legend,
} from 'recharts';

import { useApi } from '../../hooks/useApi';
import { api } from '../../api/client';

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
  dining: '餐饮', groceries: '生鲜日用', transport: '交通', shopping: '购物',
  housing: '居住', health: '医疗', education: '教育', entertainment: '娱乐',
  family: '家庭', social: '社交',
};

interface CategoryDonutProps {
  days: number;
}

export function CategoryDonut({ days }: CategoryDonutProps) {
  const { data } = useApi(
    () => api.finance.transactionsSummary('expense', days),
    [days],
  );

  const chartData = useMemo(() => {
    if (!data?.by_category.length) return null;
    return data.by_category.map((c) => ({
      name: CATEGORY_LABELS[c.category] || c.category,
      value: c.amount,
    }));
  }, [data]);

  if (!chartData) {
    return <div className="text-sm text-text-muted py-8 text-center">暂无数据</div>;
  }

  const total = chartData.reduce((s, d) => s + d.value, 0);

  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie
          data={chartData}
          innerRadius="48%"
          outerRadius="85%"
          dataKey="value"
          strokeWidth={0}
        >
          {chartData.map((_, i) => (
            <Cell key={i} fill={palette[i % palette.length]} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(v: number) => `¥${v.toLocaleString()}`}
        />
        <Legend
          wrapperStyle={{ fontSize: 11 }}
          formatter={(value: string) => value}
        />
        <text
          x="50%" y="50%"
          textAnchor="middle" dominantBaseline="central"
          fill="#e4e4e7" fontSize={14} fontFamily="var(--font-mono)"
        >
          ¥{total.toLocaleString()}
        </text>
      </PieChart>
    </ResponsiveContainer>
  );
}
