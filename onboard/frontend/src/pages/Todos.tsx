import { api } from '../api/client';
import type { AppName, Todo, TodoStatus } from '../api/types';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

const columns: { key: TodoStatus; label: string; dot: string }[] = [
  { key: 'pending', label: 'Pending', dot: 'bg-text-muted' },
  { key: 'in_progress', label: 'In Progress', dot: 'bg-warning' },
  { key: 'done', label: 'Done', dot: 'bg-success' },
];

// The most relevant timestamp for a todo depending on its status: done cards
// surface completion time, others surface creation time.
function todoTime(todo: Todo): string {
  if (todo.status === 'done') return todo.completed_at || todo.created_at;
  return todo.created_at;
}

function relativeTime(dateStr: string): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return '';
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  if (diff < 604_800_000) return `${Math.floor(diff / 86_400_000)}d ago`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function Todos({ appName }: { appName: AppName }) {
  const { data, error, loading, reload } = useApi(() => api.todos(appName), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Failed to load todos.'}</div>;
  }

  async function complete(todo: Todo) {
    await api.markTodoDone(appName, todo.id);
    reload();
  }

  return (
    <div className="space-y-5">
      <h2 className="font-serif text-2xl text-text m-0">Todos</h2>
      <div className="grid grid-cols-3 gap-4">
        {columns.map((col) => (
          <section key={col.key} className="space-y-2.5">
            <div className="flex items-center gap-2 mb-3">
              <span className={`w-2 h-2 rounded-full ${col.dot}`} />
              <span className="text-[12px] font-medium uppercase tracking-wider text-text-muted">{col.label}</span>
              <span className="ml-auto text-[11px] font-mono text-text-muted">
                {data.filter((t) => t.status === col.key).length}
              </span>
            </div>
            {data
              .filter((todo) => todo.status === col.key)
              .sort((a, b) => new Date(todoTime(b)).getTime() - new Date(todoTime(a)).getTime())
              .map((todo, index) => (
                <article
                  key={todo.id}
                  className="p-3.5 border border-border rounded-md bg-surface-1 hover:bg-surface-2 transition-colors animate-[fade-in_0.3s_ease-out_both]"
                  style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
                >
                  <div className="text-[13px] font-medium text-text mb-1">{todo.title}</div>
                  <div className="flex items-center gap-2 text-[11px] text-text-muted">
                    <span>{todo.project ?? todo.category ?? 'general'}</span>
                    <span>·</span>
                    <span className={`${todo.priority === 'high' ? 'text-danger' : todo.priority === 'medium' ? 'text-warning' : ''}`}>
                      {todo.priority}
                    </span>
                    {relativeTime(todoTime(todo)) ? (
                      <>
                        <span>·</span>
                        <span title={col.key === 'done' ? 'Completed' : 'Created'}>
                          {col.key === 'done' ? 'done ' : ''}{relativeTime(todoTime(todo))}
                        </span>
                      </>
                    ) : null}
                  </div>
                  {todo.status !== 'done' ? (
                    <button
                      onClick={() => complete(todo)}
                      className="mt-2.5 text-[11px] px-2 py-1 rounded border border-border bg-transparent text-text-secondary hover:text-text hover:border-text-muted cursor-pointer transition-colors active:scale-[0.97]"
                    >
                      ✓ Mark done
                    </button>
                  ) : null}
                </article>
              ))}
          </section>
        ))}
      </div>
    </div>
  );
}
