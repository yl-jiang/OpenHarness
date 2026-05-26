import { api } from '../api/client';
import type { AppName, Todo, TodoStatus } from '../api/types';
import { useApi } from '../hooks/useApi';

const columns: { key: TodoStatus; label: string; dot: string }[] = [
  { key: 'pending', label: 'Pending', dot: 'bg-text-muted' },
  { key: 'in_progress', label: 'In Progress', dot: 'bg-warning' },
  { key: 'done', label: 'Done', dot: 'bg-success' },
];

export function Todos({ appName }: { appName: AppName }) {
  const { data, error, loading, reload } = useApi(() => api.todos(appName), [appName]);
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
              .map((todo) => (
                <article key={todo.id} className="p-3.5 border border-border rounded-md bg-surface-1 hover:bg-surface-2 transition-colors">
                  <div className="text-[13px] font-medium text-text mb-1">{todo.title}</div>
                  <div className="flex items-center gap-2 text-[11px] text-text-muted">
                    <span>{todo.project ?? todo.category ?? 'general'}</span>
                    <span>·</span>
                    <span className={`${todo.priority === 'high' ? 'text-danger' : todo.priority === 'medium' ? 'text-warning' : ''}`}>
                      {todo.priority}
                    </span>
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
