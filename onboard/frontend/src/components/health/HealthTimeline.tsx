import type { HealthTimelineItem } from '../../api/types';

interface HealthTimelineProps {
  items: HealthTimelineItem[];
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}

const SUBJECT_LABEL: Record<string, string> = { self: '自己' };

export function HealthTimeline({ items, total, page, pageSize, onPageChange }: HealthTimelineProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  if (total === 0) {
    return <div className="text-sm text-text-muted py-8 text-center">暂无健康记录</div>;
  }

  // Build page number buttons with ellipsis
  const pageNumbers: (number | '...')[] = [];
  const maxVisible = 7;
  if (totalPages <= maxVisible) {
    for (let i = 1; i <= totalPages; i++) pageNumbers.push(i);
  } else {
    pageNumbers.push(1);
    if (page > 3) pageNumbers.push('...');
    const start = Math.max(2, page - 1);
    const end = Math.min(totalPages - 1, page + 1);
    for (let i = start; i <= end; i++) pageNumbers.push(i);
    if (page < totalPages - 2) pageNumbers.push('...');
    pageNumbers.push(totalPages);
  }

  return (
    <div>
      <div className="text-xs text-text-muted mb-3 font-mono">
        共 {total} 条记录，第 {page}/{totalPages} 页
      </div>
      <div className="space-y-1">
        {items.map((item) => {
          const subjectLabel = SUBJECT_LABEL[item.subject] ?? item.subject;
          const subjectIcon = item.subject === 'self' ? '👤' : '👶';
          return (
            <div
              key={item.id}
              className="flex items-start gap-3 px-3 py-2.5 rounded-md hover:bg-surface-2 transition-colors"
            >
              <span className="text-sm mt-0.5 flex-shrink-0">{item.icon}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 text-xs">
                  <span className="font-mono text-text-muted">{item.date}</span>
                  <span className="text-text-secondary">{subjectIcon}{subjectLabel}</span>
                  <span className="px-1.5 py-0.5 rounded bg-surface-3 text-text-secondary text-[10px] uppercase tracking-wider">
                    {item.category}
                  </span>
                  {item.severity && (
                    <span className={`text-[10px] ${
                      item.severity === 'severe' ? 'text-red-400' :
                      item.severity === 'moderate' ? 'text-yellow-400' : 'text-green-400'
                    }`}>
                      {item.severity}
                    </span>
                  )}
                </div>
                <div className="text-sm text-text mt-0.5">{item.item}</div>
                {item.description && (
                  <div className="text-xs text-text-muted mt-0.5 truncate">{item.description}</div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-center gap-1 mt-4 pt-3 border-t border-border/30">
        <button
          className="px-2 py-1 text-xs rounded text-text-muted hover:text-text hover:bg-surface-2 cursor-pointer bg-transparent border-0 disabled:opacity-30 disabled:cursor-default"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          ‹ 上一页
        </button>
        {pageNumbers.map((p, i) =>
          p === '...' ? (
            <span key={`e${i}`} className="px-1 text-xs text-text-muted">…</span>
          ) : (
            <button
              key={p}
              className={`w-7 h-7 text-xs rounded cursor-pointer border-0 transition-colors ${
                p === page
                  ? 'bg-surface-3 text-text font-medium'
                  : 'bg-transparent text-text-muted hover:text-text hover:bg-surface-2'
              }`}
              onClick={() => onPageChange(p)}
            >
              {p}
            </button>
          )
        )}
        <button
          className="px-2 py-1 text-xs rounded text-text-muted hover:text-text hover:bg-surface-2 cursor-pointer bg-transparent border-0 disabled:opacity-30 disabled:cursor-default"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          下一页 ›
        </button>
      </div>
    </div>
  );
}
