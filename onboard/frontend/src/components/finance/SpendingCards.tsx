import { StatsCard } from '../StatsCard';
import type { FinanceOverview } from '../../api/types';

interface SpendingCardsProps {
  overview: FinanceOverview | null;
}

function trendArrow(current: number, previous: number) {
  if (!previous) return '';
  if (current > previous) return '↑';
  if (current < previous) return '↓';
  return '→';
}

export function SpendingCards({ overview }: SpendingCardsProps) {
  if (!overview) {
    return (
      <>
        <StatsCard label="本月支出" value={0} icon="💸" />
        <StatsCard label="本月收入" value={0} icon="💰" />
        <StatsCard label="本月结余" value={0} icon="◆" />
        <StatsCard label="理财净盈亏" value={0} icon="📈" />
      </>
    );
  }

  const fmt = (n: number) => `¥${Math.abs(n).toLocaleString()}`;
  const netAccent = overview.month_net < 0 ? 'var(--color-danger, #f87171)' : undefined;
  const invAccent = overview.invest_net < 0 ? 'var(--color-danger, #f87171)' : undefined;

  return (
    <>
      <StatsCard
        label="本月支出"
        value={fmt(overview.month_expense)}
        icon="💸"
        hint={trendArrow(overview.month_expense, overview.prev_expense) || undefined}
      />
      <StatsCard
        label="本月收入"
        value={fmt(overview.month_income)}
        icon="💰"
        hint={trendArrow(overview.month_income, overview.prev_income) || undefined}
      />
      <StatsCard
        label="本月结余"
        value={`${overview.month_net >= 0 ? '+' : '-'}${fmt(overview.month_net)}`}
        icon="◆"
        accent={netAccent}
      />
      <StatsCard
        label="理财净盈亏"
        value={`${overview.invest_net >= 0 ? '+' : '-'}${fmt(overview.invest_net)}`}
        icon="📈"
        accent={invAccent}
      />
    </>
  );
}
