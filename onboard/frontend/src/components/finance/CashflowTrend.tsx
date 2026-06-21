import { useMemo } from 'react';
import {
  ResponsiveContainer, ComposedChart, Bar, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts';

import { useApi } from '../../hooks/useApi';
import { api } from '../../api/client';
import type { FinanceTrendItem } from '../../api/types';

const tooltipStyle = {
  background: '#1c1c21',
  border: '1px solid #2e2e33',
  borderRadius: '6px',
  fontSize: '12px',
  fontFamily: 'var(--font-mono)',
  color: '#e4e4e7',
};

interface CashflowTrendProps {
  days: number;
}

export function CashflowTrend({ days }: CashflowTrendProps) {
  const { data } = useApi(
    () => api.finance.transactionsTrend(days),
    [days],
  );

  const chartData = useMemo(() => {
    if (!data?.trend.length) return null;
    return data.trend.map((t: FinanceTrendItem) => ({
      month: t.month.slice(5),
      income: t.income,
      expense: t.expense,
      net: t.net,
    }));
  }, [data]);

  if (!chartData) {
    return <div className="text-sm text-text-muted py-8 text-center">暂无数据</div>;
  }

  const fmt = (v: number) => `¥${v.toLocaleString()}`;

  return (
    <ResponsiveContainer width="100%" height={220}>
      <ComposedChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2e2e33" />
        <XAxis dataKey="month" tick={{ fill: '#888', fontSize: 11 }} />
        <YAxis tick={{ fill: '#888', fontSize: 11 }} tickFormatter={(v) => `${v / 1000}k`} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => fmt(v)} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Bar dataKey="income" name="收入" fill="#6a9e8e" radius={[3, 3, 0, 0]} />
        <Bar dataKey="expense" name="支出" fill="#b8956a" radius={[3, 3, 0, 0]} />
        <Line type="monotone" dataKey="net" name="结余" stroke="#8b7db8" strokeWidth={2} dot={{ r: 3 }} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
