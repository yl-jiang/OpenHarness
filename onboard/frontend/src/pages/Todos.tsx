import { api } from '../api/client';
import type { AppName, Todo, TodoStatus } from '../api/types';
import { StatusBadge } from '../components/StatusBadge';
import { useApi } from '../hooks/useApi';

const columns: TodoStatus[] = ['pending', 'in_progress', 'done'];

export function Todos({ appName }: { appName: AppName }) {
  const { data, error, loading, reload } = useApi(() => api.todos(appName), [appName]);
  if (loading) {
    return <div className="skeleton-grid" />;
  }
  if (error || !data) {
    return <div className="error-state">{error ?? 'Failed to load todos.'}</div>;
  }

  async function complete(todo: Todo) {
    await api.markTodoDone(appName, todo.id);
    reload();
  }

  return (
    <div className="kanban">
      {columns.map((status) => (
        <section key={status} className="kanban-column glass-card">
          <h2>
            <StatusBadge status={status} />
          </h2>
          {data
            .filter((todo) => todo.status === status)
            .map((todo) => (
              <article key={todo.id} className="todo-card">
                <strong>{todo.title}</strong>
                <span>{todo.project ?? todo.category ?? 'general'}</span>
                <small>{todo.priority}</small>
                {todo.status !== 'done' ? <button onClick={() => complete(todo)}>Done</button> : null}
              </article>
            ))}
        </section>
      ))}
    </div>
  );
}
