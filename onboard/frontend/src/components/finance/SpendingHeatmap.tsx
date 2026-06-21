import { useMemo, useState } from 'react';

import { useApi } from '../../hooks/useApi';
import { api } from '../../api/client';
import type { FinanceDailyItem } from '../../api/types';

interface SpendingHeatmapProps {
  month: string;
  onMonthChange: (month: string) => void;
}

function getDaysInMonth(yearMonth: string): number {
  const [y, m] = yearMonth.split('-').map(Number);
  return new Date(y, m, 0).getDate();
}

function getFirstDayOfWeek(yearMonth: string): number {
  const [y, m] = yearMonth.split('-').map(Number);
  return new Date(y, m - 1, 1).getDay();
}

function shiftMonth(yearMonth: string, delta: number): string {
  const [y, m] = yearMonth.split('-').map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];

export function SpendingHeatmap({ month, onMonthChange }: SpendingHeatmapProps) {
  const { data } = useApi(
    () => api.finance.transactionsDaily(month),
    [month],
  );

  const amountMap = useMemo(() => {
    if (!data?.items) return new Map<string, number>();
    return new Map(data.items.map((d: FinanceDailyItem) => [d.date, d.amount]));
  }, [data]);

  const maxAmount = useMemo(() => {
    if (!amountMap.size) return 1;
    return Math.max(...amountMap.values(), 1);
  }, [amountMap]);

  const daysInMonth = getDaysInMonth(month);
  const firstDay = getFirstDayOfWeek(month);
  const today = new Date().toISOString().slice(0, 10);

  function dotStyle(amount: number): { background: string; size: number } {
    if (!amount) return { background: 'transparent', size: 0 };
    const intensity = amount / maxAmount;
    const size = Math.max(6, Math.round(intensity * 22));
    const alpha = 0.25 + intensity * 0.75;
    return { background: `rgba(184, 149, 106, ${alpha})`, size };
  }

  const cells: React.ReactNode[] = [];
  for (let i = 0; i < firstDay; i++) {
    cells.push(<div key={`pad-${i}`} />);
  }
  for (let day = 1; day <= daysInMonth; day++) {
    const dateStr = `${month}-${String(day).padStart(2, '0')}`;
    const amount = amountMap.get(dateStr) || 0;
    const { background, size } = dotStyle(amount);
    const isToday = dateStr === today;

    cells.push(
      <div
        key={day}
        className="flex flex-col items-center justify-center h-10 relative"
        title={amount ? `${dateStr}: ¥${amount.toLocaleString()}` : dateStr}
      >
        <span className="text-[10px] text-text-muted">{day}</span>
        {size > 0 && (
          <span
            className="rounded-full"
            style={{
              width: size,
              height: size,
              background,
              ...(isToday ? { outline: '2px solid #f87171' } : {}),
            }}
          />
        )}
        {size === 0 && isToday && (
          <span
            className="w-2 h-2 rounded-full"
            style={{ outline: '2px solid #f87171' }}
          />
        )}
      </div>,
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <button
          onClick={() => onMonthChange(shiftMonth(month, -1))}
          className="text-xs text-text-muted hover:text-text px-2 py-1"
        >
          ‹
        </button>
        <span className="text-sm font-mono text-text-secondary">{month}</span>
        <button
          onClick={() => onMonthChange(shiftMonth(month, 1))}
          className="text-xs text-text-muted hover:text-text px-2 py-1"
        >
          ›
        </button>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center">
        {WEEKDAYS.map((d) => (
          <div key={d} className="text-[10px] text-text-muted py-1">{d}</div>
        ))}
        {cells}
      </div>
    </div>
  );
}
