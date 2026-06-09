import { useState } from 'react';
import type { ReactNode } from 'react';

import { EmptyState } from './EmptyState';

interface Column<T> {
  key: string;
  title: string;
  render: (row: T) => ReactNode;
  /** If provided, enables sorting using this accessor */
  sortValue?: (row: T) => string | number;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  emptyText?: string;
  emptyIcon?: ReactNode;
  pageSize?: number;
}

type SortDir = 'asc' | 'desc';

export function DataTable<T>({ columns, rows, emptyText = 'No data yet.', emptyIcon, pageSize }: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [page, setPage] = useState(0);

  if (rows.length === 0) {
    return <EmptyState icon={emptyIcon} title={emptyText} />;
  }

  // Sort
  let sorted = rows;
  if (sortKey) {
    const col = columns.find((c) => c.key === sortKey);
    if (col?.sortValue) {
      const accessor = col.sortValue;
      sorted = [...rows].sort((a, b) => {
        const va = accessor(a);
        const vb = accessor(b);
        const cmp = typeof va === 'number' && typeof vb === 'number'
          ? va - vb
          : String(va).localeCompare(String(vb));
        return sortDir === 'asc' ? cmp : -cmp;
      });
    }
  }

  // Paginate
  const effectivePageSize = pageSize ?? 0; // 0 = no pagination
  const totalPages = effectivePageSize > 0 ? Math.ceil(sorted.length / effectivePageSize) : 1;
  const paged = effectivePageSize > 0 ? sorted.slice(page * effectivePageSize, (page + 1) * effectivePageSize) : sorted;

  function handleSort(key: string) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
      setPage(0);
    }
  }

  return (
    <div className="space-y-0">
      <div className="border border-border rounded-lg bg-surface-1 overflow-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-border">
              {columns.map((column) => {
                const isSortable = !!column.sortValue;
                const isSorted = sortKey === column.key;
                return (
                  <th
                    key={column.key}
                    className={`px-4 py-2.5 text-left text-[11px] uppercase tracking-wider text-text-muted font-medium ${
                      isSortable ? 'cursor-pointer select-none hover:text-text-secondary transition-colors' : ''
                    }`}
                    onClick={isSortable ? () => handleSort(column.key) : undefined}
                    aria-sort={isSorted ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
                  >
                    <span className="inline-flex items-center gap-1">
                      {column.title}
                      {isSortable && (
                        <span className={`text-[9px] ${isSorted ? 'text-text' : 'text-text-muted/40'}`}>
                          {isSorted ? (sortDir === 'asc' ? '▲' : '▼') : '⇅'}
                        </span>
                      )}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {paged.map((row, index) => (
              <tr
                key={index}
                className="border-b border-border-subtle last:border-0 hover:bg-surface-2 transition-colors animate-[fade-in_0.3s_ease-out_both]"
                style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
              >
                {columns.map((column) => (
                  <td key={column.key} className="px-4 py-3 text-[13px] text-text-secondary align-top">
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {effectivePageSize > 0 && totalPages > 1 && (
        <div className="flex items-center justify-between px-1 pt-3">
          <span className="text-[11px] font-mono text-text-muted">
            {page * effectivePageSize + 1}–{Math.min((page + 1) * effectivePageSize, sorted.length)} of {sorted.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="text-[11px] px-2 py-1 rounded border border-border bg-transparent text-text-secondary hover:text-text cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              ← Prev
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="text-[11px] px-2 py-1 rounded border border-border bg-transparent text-text-secondary hover:text-text cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Next →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
