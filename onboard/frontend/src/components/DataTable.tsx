import type { ReactNode } from 'react';

interface Column<T> {
  key: string;
  title: string;
  render: (row: T) => ReactNode;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  emptyText?: string;
}

export function DataTable<T>({ columns, rows, emptyText = 'No data yet.' }: DataTableProps<T>) {
  if (rows.length === 0) {
    return (
      <div className="border border-border rounded-lg bg-surface-1 p-8 text-center text-text-muted text-sm">
        {emptyText}
      </div>
    );
  }
  return (
    <div className="border border-border rounded-lg bg-surface-1 overflow-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-border">
            {columns.map((column) => (
              <th
                key={column.key}
                className="px-4 py-2.5 text-left text-[11px] uppercase tracking-wider text-text-muted font-medium"
              >
                {column.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-border-subtle last:border-0 hover:bg-surface-2 transition-colors">
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
  );
}
