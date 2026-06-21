import { useMemo } from 'react';
import {
  ResponsiveContainer, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from 'recharts';

import { useApi } from '../../hooks/useApi';
import { api } from '../../api/client';

const tooltipStyle = {
  background: '#1c1c21',
  border: '1px solid #2e2e33',
  borderRadius: '6px',
  fontSize: '12px',
  fontFamily: 'var(--font-mono)',
  color: '#e4e4e7',
};

interface IncomeExpenseTrendProps {
  days: number;
}

export function IncomeExpenseTrend({ days }: IncomeExpenseTrendProps) {
  const { data } = useApi(
    () => api.finance.transactionsTrend(days),
    [days],
  );

  const chartData = useMemo(() => {
    if (!data?.trend.length) return null;
    return data.trend.map((t) => ({
      month: t.month.slice(5),
      income: t.income,
      expense: t.expense,
    }));
  }, [data]);

  if (!chartData) {
    return <div className="text-sm text-text-muted py-8 text-center">暂无数据</div>;
  }

  const fmt = (v: number) => `¥${v.toLocaleString()}`;

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={chartData}>
        <defs>
          <linearGradient id="incomeGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#6a9e8e" stopOpacity={0.35} />
            <stop offset="95%" stopColor="#6a9e8e" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="expenseGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#b8956a" stopOpacity={0.35} />
            <stop offset="95%" stopColor="#b8956a" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#2e2e33" />
        <XAxis dataKey="month" tick={{ fill: '#888', fontSize: 11 }} />
        <YAxis tick={{ fill: '#888', fontSize: 11 }} tickFormatter={(v) => `${v / 1000}k`} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => fmt(v)} />
        <Area
          type="monotone"
          dataKey="income"
          name="收入"
          stroke="#6a9e8e"
          fill="url(#incomeGrad)"
          strokeWidth={2}
          dot={{ r: 3 }}
          activeDot={{ r: 5 }}
        />
        <Area
          type="monotone"
          dataKey="expense"
          name="支出"
          stroke="#b8956a"
          fill="url(#expenseGrad)"
          strokeWidth={2}
          strokeDasharray="6 4"
          dot={{ r: 3 }}
          activeDot={{ r: 5 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
