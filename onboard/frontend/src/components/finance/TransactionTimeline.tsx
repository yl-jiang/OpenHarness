import { useMemo, useState } from 'react';

import { useApi } from '../../hooks/useApi';
import { api } from '../../api/client';
import type { SoloFinanceTransaction, FinanceTxnType } from '../../api/types';

const PAGE_SIZE = 10;

const TYPE_FILTERS: { key: FinanceTxnType | 'all'; label: string; icon: string }[] = [
  { key: 'all', label: '全部', icon: '◎' },
  { key: 'expense', label: '支出', icon: '💸' },
  { key: 'income', label: '收入', icon: '💰' },
  { key: 'invest_gain', label: '理财盈', icon: '📈' },
  { key: 'invest_loss', label: '理财亏', icon: '📉' },
  { key: 'transfer', label: '转账', icon: '⇄' },
];

const TYPE_ICONS: Record<string, string> = {
  expense: '💸', income: '💰', transfer: '⇄',
  invest_gain: '📈', invest_loss: '📉',
};

export function TransactionTimeline() {
  const [expanded, setExpanded] = useState(false);
  const [typeFilter, setTypeFilter] = useState<FinanceTxnType | 'all'>('all');
  const [page, setPage] = useState(0);

  const { data } = useApi(
    () => api.finance.transactions({ limit: 500, ...(typeFilter !== 'all' ? { type: typeFilter } : {}) }),
    [typeFilter],
  );

  const items = data?.items ?? [];
  const total = items.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageItems = useMemo(
    () => items.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [items, page],
  );

  // Reset page when filter changes
  const handleFilterChange = (key: FinanceTxnType | 'all') => {
    setTypeFilter(key);
    setPage(0);
  };

  return (
    <div>
      {/* Collapse toggle */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-sm text-text-muted hover:text-text transition-colors mb-3"
      >
        <span className="text-xs">{expanded ? '▾' : '▸'}</span>
        <span>{expanded ? '收起' : '展开'}</span>
        <span className="font-mono text-xs text-text-muted">
          ({total} 条)
        </span>
      </button>

      {expanded && (
        <>
          {/* Type filter chips */}
          <div className="flex flex-wrap gap-1.5 mb-4">
            {TYPE_FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => handleFilterChange(f.key)}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                  typeFilter === f.key
                    ? 'border-accent-solo text-accent-solo bg-accent-solo/10'
                    : 'border-border text-text-muted hover:text-text hover:border-text-muted'
                }`}
              >
                {f.icon} {f.label}
              </button>
            ))}
          </div>

          {!pageItems.length ? (
            <div className="text-sm text-text-muted py-8 text-center">暂无流水</div>
          ) : (
            <>
              <div className="space-y-1.5">
                {pageItems.map((t: SoloFinanceTransaction) => {
                  const isGain = t.type === 'invest_gain' || t.type === 'income';
                  const isLoss = t.type === 'invest_loss' || t.type === 'expense';
                  const sign = isGain ? '+' : isLoss ? '-' : '';
                  const color = isGain
                    ? 'text-emerald-400'
                    : isLoss
                      ? 'text-red-400'
                      : 'text-text-secondary';

                  return (
                    <div
                      key={t.id}
                      className="flex items-center gap-3 px-3 py-2 rounded-md bg-surface-2/50 hover:bg-surface-2 text-sm"
                    >
                      <span className="text-text-muted font-mono text-xs w-12 shrink-0">
                        {t.date.slice(5)}
                      </span>
                      <span className="w-5 text-center">{TYPE_ICONS[t.type] || '◎'}</span>
                      <span className="text-text-secondary truncate flex-1">
                        {t.description || t.category}
                      </span>
                      <span className={`font-mono text-xs shrink-0 ${color}`}>
                        {sign}¥{t.amount.toLocaleString()}
                      </span>
                    </div>
                  );
                })}
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2 mt-4">
                  <button
                    onClick={() => setPage(Math.max(0, page - 1))}
                    disabled={page === 0}
                    className="text-xs px-2.5 py-1 rounded border border-border text-text-muted hover:text-text hover:border-text-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    ‹ 上一页
                  </button>
                  <span className="text-xs font-mono text-text-muted">
                    {page + 1} / {totalPages}
                  </span>
                  <button
                    onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                    disabled={page >= totalPages - 1}
                    className="text-xs px-2.5 py-1 rounded border border-border text-text-muted hover:text-text hover:border-text-muted transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    下一页 ›
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
