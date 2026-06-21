import { useMemo } from 'react';
import {
  ResponsiveContainer, RadialBarChart, RadialBar, Tooltip, Legend,
} from 'recharts';

import { useApi } from '../../hooks/useApi';
import { api } from '../../api/client';
import type { FinanceBudgetWithUtilization } from '../../api/types';

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

function utilizationColor(u: number): string {
  if (u >= 1.0) return '#f87171';
  if (u >= 0.8) return '#fb923c';
  if (u >= 0.6) return '#fbbf24';
  return '#34d399';
}

export function BudgetRings() {
  const { data } = useApi(() => api.finance.budgets(), []);

  const chartData = useMemo(() => {
    if (!data?.items.length) return null;
    return data.items.map((b: FinanceBudgetWithUtilization, i: number) => ({
      name: b.category ? (CATEGORY_LABELS[b.category] || b.category) : '总预算',
      value: Math.min(b.utilization * 100, 120),
      fill: utilizationColor(b.utilization),
      utilization: b.utilization,
      amount: b.amount,
      spent: b.spent,
    }));
  }, [data]);

  if (!chartData) {
    return <div className="text-sm text-text-muted py-8 text-center">暂无预算</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <RadialBarChart
        innerRadius="20%"
        outerRadius="90%"
        data={chartData}
        startAngle={90}
        endAngle={-270}
      >
        <RadialBar
          dataKey="value"
          cornerRadius={4}
          background={{ fill: '#1e1e23' }}
        />
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(_: number, __: string, entry: { payload?: { spent?: number; amount?: number } }) => {
            const p = entry?.payload;
            return p ? `¥${p.spent?.toLocaleString()} / ¥${p.amount?.toLocaleString()}` : '';
          }}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
      </RadialBarChart>
    </ResponsiveContainer>
  );
}
