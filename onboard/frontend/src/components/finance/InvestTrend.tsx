import { useMemo } from 'react';
import {
  ResponsiveContainer, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts';

import { useApi } from '../../hooks/useApi';
import { api } from '../../api/client';
import type { SoloFinanceTransaction } from '../../api/types';

const GAIN_COLOR = '#34d399';
const LOSS_COLOR = '#f87171';

const tooltipStyle = {
  background: '#1c1c21',
  border: '1px solid #2e2e33',
  borderRadius: '6px',
  fontSize: '12px',
  fontFamily: 'var(--font-mono)',
  color: '#e4e4e7',
};

function getCurrentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function getDaysInMonth(month: string): number {
  const [y, m] = month.split('-').map(Number);
  return new Date(y, m, 0).getDate();
}

export function InvestTrend() {
  const month = getCurrentMonth();
  const dateFrom = `${month}-01`;
  const daysInMonth = getDaysInMonth(month);
  const dateTo = `${month}-${String(daysInMonth).padStart(2, '0')}`;

  const { data } = useApi(
    () => api.finance.transactions({ date_from: dateFrom, date_to: dateTo, limit: 500 }),
    [month],
  );

  const chartData = useMemo(() => {
    const gainByDay = new Map<string, number>();
    const lossByDay = new Map<string, number>();

    if (data?.items) {
      for (const t of data.items as SoloFinanceTransaction[]) {
        if (t.currency !== 'CNY') continue;
        const day = t.date.slice(8);
        if (t.type === 'invest_gain') {
          gainByDay.set(day, (gainByDay.get(day) || 0) + t.amount);
        } else if (t.type === 'invest_loss') {
          lossByDay.set(day, (lossByDay.get(day) || 0) + t.amount);
        }
      }
    }

    return Array.from({ length: daysInMonth }, (_, i) => {
      const day = String(i + 1).padStart(2, '0');
      const gain = gainByDay.get(day) || 0;
      const loss = lossByDay.get(day) || 0;
      return { day, gain, loss, net: gain - loss };
    });
  }, [data, daysInMonth]);

  const hasData = chartData.some((d) => d.gain > 0 || d.loss > 0);
  if (!hasData) {
    return <div className="text-sm text-text-muted py-8 text-center">暂无理财数据</div>;
  }

  const allValues = chartData.flatMap((d) => [d.gain, -d.loss]);
  const vMax = Math.max(0, ...allValues);
  const vMin = Math.min(0, ...allValues);
  const pad = (vMax - vMin) * 0.15 || 100;
  const yMax = Math.ceil(vMax + pad);
  const yMin = Math.floor(vMin - pad);

  const tickDays = chartData
    .filter((_, i) => i === 0 || (i + 1) % 5 === 0 || i === chartData.length - 1)
    .map((d) => d.day);

  return (
    <div className="space-y-3">
      <div className="h-[220px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
            <defs>
              <linearGradient id="investGainGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={GAIN_COLOR} stopOpacity={0.28} />
                <stop offset="100%" stopColor={GAIN_COLOR} stopOpacity={0.02} />
              </linearGradient>
              <linearGradient id="investLossGrad" x1="0" y1="1" x2="0" y2="0">
                <stop offset="0%" stopColor={LOSS_COLOR} stopOpacity={0.28} />
                <stop offset="100%" stopColor={LOSS_COLOR} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#1f1f24" strokeDasharray="2 4" />
            <XAxis
              dataKey="day"
              stroke="#63636e"
              tick={{ fontSize: 10, fill: '#a1a1aa' }}
              ticks={tickDays}
              tickLine={{ stroke: '#2e2e33' }}
            />
            <YAxis
              domain={[yMin, yMax]}
              tick={{ fontSize: 11, fill: '#a1a1aa' }}
              tickLine={{ stroke: '#2e2e33' }}
              axisLine={{ stroke: '#2e2e33' }}
              stroke="#63636e"
              tickFormatter={(v) => v >= 1000 || v <= -1000 ? `${v / 1000}k` : String(v)}
              allowDecimals={false}
              width={40}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              cursor={{ stroke: '#3f3f46', strokeWidth: 1 }}
              formatter={(v: number, name: string) => [
                `¥${v.toLocaleString()}`,
                name === 'gain' ? '收益' : '亏损',
              ]}
              labelFormatter={(label) => `${month}-${label}`}
            />
            <ReferenceLine y={0} stroke="#555" strokeDasharray="3 3" />
            <Area
              type="monotone"
              dataKey="gain"
              name="gain"
              stroke={GAIN_COLOR}
              strokeWidth={1.75}
              fill="url(#investGainGrad)"
              dot={{ r: 2.5, fill: GAIN_COLOR, strokeWidth: 0 }}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="loss"
              name="loss"
              stroke={LOSS_COLOR}
              strokeWidth={1.75}
              strokeDasharray="6 4"
              fill="url(#investLossGrad)"
              dot={{ r: 2.5, fill: LOSS_COLOR, strokeWidth: 0 }}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="flex gap-3 text-[11px] font-mono text-text-secondary">
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2 w-4 rounded-sm"
            style={{ backgroundColor: GAIN_COLOR, opacity: 0.35, boxShadow: `inset 0 0 0 1px ${GAIN_COLOR}` }}
          />
          收益
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2 w-4 rounded-sm border border-dashed"
            style={{ borderColor: LOSS_COLOR }}
          />
          亏损
        </span>
      </div>
    </div>
  );
}
