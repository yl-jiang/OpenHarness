import { useMemo } from 'react';
import {
  ResponsiveContainer, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
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

interface InvestTrendProps {
  days: number;
}

export function InvestTrend({ days }: InvestTrendProps) {
  const { data } = useApi(
    () => api.finance.investTrend(days),
    [days],
  );

  const chartData = useMemo(() => {
    if (!data?.trend.length) return null;
    return data.trend.map((t) => ({
      month: t.month.slice(5),
      net: t.net,
    }));
  }, [data]);

  if (!chartData) {
    return <div className="text-sm text-text-muted py-8 text-center">暂无理财数据</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={chartData}>
        <defs>
          <linearGradient id="investGradPos" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#34d399" stopOpacity={0.4} />
            <stop offset="95%" stopColor="#34d399" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#2e2e33" />
        <XAxis dataKey="month" tick={{ fill: '#888', fontSize: 11 }} />
        <YAxis tick={{ fill: '#888', fontSize: 11 }} tickFormatter={(v) => `${v / 1000}k`} />
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(v: number) => `¥${v.toLocaleString()}`}
        />
        <ReferenceLine y={0} stroke="#555" strokeDasharray="3 3" />
        <Area
          type="monotone"
          dataKey="net"
          name="理财净盈亏"
          stroke="#34d399"
          fill="url(#investGradPos)"
          strokeWidth={2}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
