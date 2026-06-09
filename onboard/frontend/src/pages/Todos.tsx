import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  closestCenter,
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

import { api } from '../api/client';
import type { AppName, Todo, TodoStatus } from '../api/types';
import { useToast } from '../components/ToastProvider';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

const columns: { key: TodoStatus; label: string; dot: string }[] = [
  { key: 'pending', label: 'Pending', dot: 'bg-text-muted' },
  { key: 'in_progress', label: 'In Progress', dot: 'bg-warning' },
  { key: 'done', label: 'Done', dot: 'bg-success' },
];

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

function isOverdue(todo: Todo): boolean {
  if (todo.status === 'done' || !todo.due_date) return false;
  const due = new Date(todo.due_date);
  if (Number.isNaN(due.getTime())) return false;
  return due.getTime() < Date.now();
}

/** Render a todo card's content (shared between SortableCard and DragOverlay). */
function CardContent({ todo, overdue }: { todo: Todo; overdue: boolean }) {
  return (
    <>
      <div className="text-[13px] font-medium text-text mb-1">{todo.title}</div>
      <div className="flex items-center gap-2 text-[11px] text-text-muted flex-wrap">
        <span>{todo.project ?? todo.category ?? 'general'}</span>
        <span aria-hidden="true">&middot;</span>
        <span className={`${todo.priority === 'high' ? 'text-danger' : todo.priority === 'medium' ? 'text-warning' : ''}`}>
          {todo.priority}
        </span>
        {overdue && (
          <>
            <span aria-hidden="true">&middot;</span>
            <span className="text-danger font-medium">overdue</span>
          </>
        )}
        {relativeTime(todoTime(todo)) ? (
          <>
            <span aria-hidden="true">&middot;</span>
            <span>{relativeTime(todoTime(todo))}</span>
          </>
        ) : null}
      </div>
    </>
  );
}

/** A single sortable card inside a column. */
function SortableCard({
  todo,
  isDragging,
}: {
  todo: Todo;
  isDragging: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging: isSortableDragging } = useSortable({ id: todo.id });
  const overdue = isOverdue(todo);

  return (
    <article
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.3 : 1,
      }}
      className={`p-3.5 border rounded-md bg-surface-1 hover:bg-surface-2 transition-colors cursor-grab active:cursor-grabbing ${
        overdue ? 'border-danger/40' : 'border-border'
      } ${isSortableDragging ? 'z-50' : ''}`}
      {...attributes}
      {...listeners}
    >
      <CardContent todo={todo} overdue={overdue} />
    </article>
  );
}

export function Todos({ appName }: { appName: AppName }) {
  const { data, error, loading, reload } = useApi(() => api.todos(appName), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  const [priorityFilter, setPriorityFilter] = useState<string>('all');
  const [localItems, setLocalItems] = useState<Todo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const { toast } = useToast();

  // Sync server data → local state
  useEffect(() => {
    if (data) setLocalItems([...data]);
  }, [data]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const filtered = useMemo(() => {
    const base = priorityFilter === 'all' ? localItems : localItems.filter((t) => t.priority === priorityFilter);
    return base;
  }, [localItems, priorityFilter]);

  const columnItems = useMemo(() => {
    const map: Record<string, Todo[]> = {};
    for (const col of columns) {
      map[col.key] = filtered
        .filter((t) => t.status === col.key)
        .sort((a, b) => new Date(todoTime(b)).getTime() - new Date(todoTime(a)).getTime());
    }
    return map;
  }, [filtered]);

  const findColumn = useCallback(
    (id: string | number) => columns.find((col) => columnItems[col.key].some((t) => t.id === id))?.key,
    [columnItems],
  );

  const activeTodo = activeId ? localItems.find((t) => t.id === activeId) : null;

  // --- Drag handlers ---

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string);
  }

  function handleDragOver(event: DragOverEvent) {
    const { active, over } = event;
    if (!over) return;

    const activeCol = findColumn(active.id as string);
    const overCol = findColumn(over.id as string);
    if (!activeCol || !overCol || activeCol === overCol) return;

    // Live cross-column reorder (visual only)
    setLocalItems((prev) => {
      const activeItem = prev.find((t) => t.id === active.id);
      if (!activeItem) return prev;
      const overIndex = prev.findIndex((t) => t.id === over.id);
      const rest = prev.filter((t) => t.id !== active.id);
      const updated = { ...activeItem, status: overCol };
      if (overIndex === -1) {
        rest.push(updated);
      } else {
        const idx = rest.findIndex((t) => t.id === over.id);
        rest.splice(idx, 0, updated);
      }
      return rest;
    });
  }

  async function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    const id = active.id as string;
    setActiveId(null);

    if (!over) return;

    const original = data?.find((t) => t.id === id);
    if (!original) return;

    const targetCol = findColumn(over.id as string);
    if (!targetCol || original.status === targetCol) return;

    // Optimistic: status already updated via onDragOver
    try {
      if (targetCol === 'done') {
        await api.markTodoDone(appName, id);
      } else if (targetCol === 'in_progress') {
        await api.startTodo(appName, id);
      } else {
        await api.revertTodo(appName, id);
      }
      toast(`"${original.title}" → ${columns.find((c) => c.key === targetCol)?.label}`, 'success');
      reload();
    } catch {
      setLocalItems([...(data ?? [])]);
      toast('Failed to update todo', 'error');
    }
  }

  // --- Render ---

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text" role="alert">{error ?? 'Failed to load todos.'}</div>;
  }

  return (
    <div className="space-y-5">
      <div className="flex items-baseline justify-between flex-wrap gap-3">
        <h2 className="font-serif text-2xl text-text m-0">Todos</h2>
        <div className="flex items-center gap-3">
          <select
            value={priorityFilter}
            onChange={(e) => setPriorityFilter(e.target.value)}
            className="text-[12px] px-2 py-1 rounded-md border border-border bg-surface-2 text-text-secondary cursor-pointer focus:outline-none"
            aria-label="Filter by priority"
          >
            <option value="all">All priorities</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <span className="text-[11px] font-mono text-text-muted">{data.length} total</span>
        </div>
      </div>

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
      >
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {columns.map((col) => (
            <section key={col.key} className="space-y-2.5 min-h-[120px]">
              <div className="flex items-center gap-2 mb-3">
                <span className={`w-2 h-2 rounded-full ${col.dot}`} />
                <span className="text-[12px] font-medium uppercase tracking-wider text-text-muted">{col.label}</span>
                <span className="ml-auto text-[11px] font-mono text-text-muted">
                  {columnItems[col.key].length}
                </span>
              </div>
              <SortableContext
                items={columnItems[col.key].map((t) => t.id)}
                strategy={verticalListSortingStrategy}
              >
                <div className="flex flex-col gap-2.5">
                  {columnItems[col.key].map((todo) => (
                    <SortableCard
                      key={todo.id}
                      todo={todo}
                      isDragging={activeId === todo.id}
                    />
                  ))}
                </div>
              </SortableContext>
            </section>
          ))}
        </div>

        <DragOverlay dropAnimation={{ duration: 200, easing: 'ease' }}>
          {activeTodo ? (
            <article
              className={`p-3.5 border rounded-md bg-surface-2 border-border shadow-lg`}
              style={{ cursor: 'grabbing' }}
            >
              <CardContent todo={activeTodo} overdue={isOverdue(activeTodo)} />
            </article>
          ) : null}
        </DragOverlay>
      </DndContext>
    </div>
  );
}
