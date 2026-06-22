import { useState } from 'react';

import { SciFiBackground } from '../components/SciFiBackground';
import { SpendingCards } from '../components/finance/SpendingCards';
import { CashflowTrend } from '../components/finance/CashflowTrend';
import { CategoryDonut } from '../components/finance/CategoryDonut';
import { BudgetRings } from '../components/finance/BudgetRings';
import { CategoryRanking } from '../components/finance/CategoryRanking';
import { InvestTrend } from '../components/finance/InvestTrend';
import { SpendingHeatmap } from '../components/finance/SpendingHeatmap';
import { TransactionTimeline } from '../components/finance/TransactionTimeline';
import { useApi } from '../hooks/useApi';
import { api } from '../api/client';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="p-5 rounded-lg border border-border bg-surface-1">
      <h3 className="text-sm font-medium text-text mb-4">{title}</h3>
      {children}
    </section>
  );
}

const DAY_OPTIONS = [
  { label: '3M', value: 90 },
  { label: '6M', value: 180 },
  { label: '12M', value: 365 },
];

export function Finance() {
  const [selectedDays, setSelectedDays] = useState(180);
  const [chartMonth, setChartMonth] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  });

  const { data: overview } = useApi(() => api.finance.overview(), []);

  return (
    <>
      <SciFiBackground accent="#d4a574" />
      <div className="relative space-y-6" style={{ zIndex: 1 }}>
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-serif text-text">Finance</h1>
            <p className="text-sm text-text-muted mt-1">个人消费追踪与预算</p>
          </div>
          <div className="flex gap-1">
            {DAY_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setSelectedDays(opt.value)}
                className={`text-xs px-2.5 py-1 rounded border font-mono transition-colors ${
                  selectedDays === opt.value
                    ? 'border-accent-solo text-accent-solo bg-accent-solo/10'
                    : 'border-border text-text-muted hover:text-text'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Zone 1: Monthly overview cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <SpendingCards overview={overview} />
        </div>

        {/* Zone 2: Daily cashflow */}
        <Section title="每日收支">
          <CategoryRanking />
        </Section>

        {/* Zone 3: Category donut + Budget rings */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Section title="消费类别构成">
            <CategoryDonut days={selectedDays} />
          </Section>
          <Section title="预算追踪">
            <BudgetRings />
          </Section>
        </div>

        {/* Zone 4: Category ranking + Invest trend */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Section title="月度收支趋势">
            <CashflowTrend days={selectedDays} />
          </Section>
          <Section title="理财盈亏趋势">
            <InvestTrend days={selectedDays} />
          </Section>
        </div>

        {/* Zone 5: Spending heatmap calendar */}
        <Section title="消费日历">
          <SpendingHeatmap month={chartMonth} onMonthChange={setChartMonth} />
        </Section>

        {/* Zone 6: Transaction timeline */}
        <Section title="最近流水">
          <TransactionTimeline />
        </Section>
      </div>
    </>
  );
}
