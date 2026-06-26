import { useMemo } from 'react';
import {
  ResponsiveContainer, ComposedChart, Bar, Line, ReferenceLine,
  XAxis, YAxis, CartesianGrid, Tooltip,
} from 'recharts';

import { useApi } from '../../hooks/useApi';
import { api } from '../../api/client';
import type { SoloFinanceTransaction } from '../../api/types';

const GAIN_COLOR = '#f87171';
const LOSS_COLOR = '#34d399';
const NET_COLOR = '#a1a1aa';

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

function formatSeriesValue(v: number, name: string): string {
  if (name === 'net') {
    const sign = v > 0 ? '+' : v < 0 ? '-' : '';
    return `${sign}¥${Math.abs(v).toLocaleString()}`;
  }
  return `¥${Math.abs(v).toLocaleString()}`;
}

function seriesLabel(name: string): string {
  if (name === 'gain') return '收益';
  if (name === 'loss') return '亏损';
  return '净值';
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

    const mm = month.slice(5);
    return Array.from({ length: daysInMonth }, (_, i) => {
      const day = String(i + 1).padStart(2, '0');
      const label = `${mm}/${day}`;
      const gain = gainByDay.get(day) || 0;
      const loss = -(lossByDay.get(day) || 0);
      return { day: label, gain, loss, net: gain + loss };
    });
  }, [data, daysInMonth, month]);

  const hasData = chartData.some((d) => Math.abs(d.gain) > 0 || Math.abs(d.loss) > 0);
  if (!hasData) {
    return <div className="text-sm text-text-muted py-8 text-center">暂无理财数据</div>;
  }

  const allValues = chartData.flatMap((d) => [d.gain, d.loss, d.net]);
  const dataMax = Math.max(0, ...allValues);
  const dataMin = Math.min(0, ...allValues);
  const yMax = dataMax <= 0 ? 100 : Math.ceil(dataMax * 1.15);
  const yMin = dataMin >= 0 ? -Math.ceil(yMax * 0.15) : Math.floor(dataMin * 1.15);

  const tickDays = chartData
    .filter((_, i) => i === 0 || (i + 1) % 5 === 0 || i === chartData.length - 1)
    .map((d) => d.day);

  return (
    <div className="space-y-3">
      <div className="h-[220px]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={chartData}
            margin={{ top: 8, right: 12, bottom: 4, left: 4 }}
            barCategoryGap="25%"
          >
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
              tickFormatter={(v) => Math.abs(v) >= 1000 ? `${v / 1000}k` : String(v)}
              allowDecimals={false}
              width={40}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              cursor={{ fill: '#2e2e33', opacity: 0.25 }}
              formatter={(v: number, name: string) => [
                formatSeriesValue(v, name),
                seriesLabel(name),
              ]}
              labelFormatter={(label) => `${month.slice(0, 7)}-${label.replace('/', '-')}`}
            />
            <ReferenceLine y={0} stroke="#63636e" strokeDasharray="2 2" />
            <Bar
              dataKey="gain"
              name="gain"
              fill={GAIN_COLOR}
              barSize={8}
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
            <Bar
              dataKey="loss"
              name="loss"
              fill={LOSS_COLOR}
              barSize={8}
              radius={[0, 0, 3, 3]}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="net"
              name="net"
              stroke={NET_COLOR}
              strokeWidth={1.5}
              dot={{ r: 1.5, fill: NET_COLOR, strokeWidth: 0 }}
              activeDot={{ r: 3, strokeWidth: 0 }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="flex gap-3 text-[11px] font-mono text-text-secondary">
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2 w-4 rounded-sm"
            style={{ backgroundColor: GAIN_COLOR, opacity: 0.8 }}
          />
          收益
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2 w-4 rounded-sm"
            style={{ backgroundColor: LOSS_COLOR, opacity: 0.8 }}
          />
          亏损
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-0.5 w-4 rounded-full"
            style={{ backgroundColor: NET_COLOR }}
          />
          净值趋势
        </span>
      </div>
    </div>
  );
}
