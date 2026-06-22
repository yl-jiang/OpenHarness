import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  closestCorners,
  DndContext,
  DragOverlay,
  PointerSensor,
  useDroppable,
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
import { ConfirmDialog } from '../components/ConfirmDialog';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

const columns: { key: TodoStatus; label: string; dot: string }[] = [
  { key: 'pending', label: 'Pending', dot: 'bg-text-muted' },
  { key: 'in_progress', label: 'In Progress', dot: 'bg-warning' },
  { key: 'done', label: 'Done', dot: 'bg-success' },
  { key: 'cancelled', label: 'Cancelled', dot: 'bg-danger' },
];

/** Prefix column IDs so they never collide with todo IDs. */
const colId = (status: TodoStatus) => `col:${status}`;
const statusFromColId = (id: string): TodoStatus | null => {
  if (typeof id === 'string' && id.startsWith('col:')) return id.slice(4) as TodoStatus;
  return null;
};

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
  if (todo.status === 'done' || todo.status === 'cancelled' || !todo.due_date) return false;
  const due = new Date(todo.due_date);
  if (Number.isNaN(due.getTime())) return false;
  return due.getTime() < Date.now();
}

// --- Sub-components ---

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

function CardActions({
  todo,
  onAction,
}: {
  todo: Todo;
  onAction: (todo: Todo, target: TodoStatus | 'delete') => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menuOpen]);

  const quickAction = (): { label: string; target: TodoStatus } | null => {
    if (todo.status === 'done' || todo.status === 'cancelled') return { label: '↩', target: 'pending' };
    return { label: '✓', target: 'done' };
  };

  const qa = quickAction();

  const menuItems: { label: string; target: TodoStatus | 'delete'; danger?: boolean }[] = [];
  if (todo.status === 'pending') menuItems.push({ label: '▶ Start', target: 'in_progress' });
  if (todo.status === 'in_progress') menuItems.push({ label: '← Pending', target: 'pending' });
  if (todo.status === 'done') menuItems.push({ label: '← Pending', target: 'pending' });
  if (todo.status === 'cancelled') menuItems.push({ label: '← Pending', target: 'pending' });
  if (todo.status !== 'done' && todo.status !== 'cancelled') {
    menuItems.push({ label: '✕ Cancel', target: 'cancelled', danger: true });
  }
  menuItems.push({ label: '🗑 Delete', target: 'delete', danger: true });

  return (
    <div
      className="flex items-center gap-1 shrink-0"
      onPointerDown={(e) => e.stopPropagation()}
    >
      {qa && (
        <button
          onClick={() => onAction(todo, qa.target)}
          className="opacity-0 group-hover:opacity-100 p-1 rounded text-text-muted hover:text-text hover:bg-surface-3 transition-all text-[13px] leading-none"
          title={qa.target === 'done' ? 'Mark done' : 'Reopen'}
        >
          {qa.label}
        </button>
      )}
      <div ref={menuRef} className="relative">
        <button
          type="button"
          onClick={() => setMenuOpen(!menuOpen)}
          className="opacity-0 group-hover:opacity-100 p-1 rounded text-text-muted hover:text-text hover:bg-surface-3 transition-all"
          title="More actions"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <circle cx="8" cy="3" r="1.5" />
            <circle cx="8" cy="8" r="1.5" />
            <circle cx="8" cy="13" r="1.5" />
          </svg>
        </button>
        {menuOpen && (
          <div className="absolute right-0 top-full mt-1 z-20 min-w-[130px] bg-surface-2 border border-border rounded-md shadow-lg py-1">
            {menuItems.map((item, i) => {
              const showDivider = item.danger && i > 0 && !menuItems[i - 1].danger;
              return (
                <div key={item.target}>
                  {showDivider && <div className="border-t border-border-subtle my-1" />}
                  <button
                    type="button"
                    onClick={() => { setMenuOpen(false); onAction(todo, item.target); }}
                    className={`w-full text-left px-3 py-1.5 text-[12px] transition-colors ${
                      item.danger
                        ? 'text-red-400 hover:bg-red-500/10'
                        : 'text-text hover:bg-surface-3'
                    }`}
                  >
                    {item.label}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function SortableCard({
  todo,
  isDragging,
  onAction,
}: {
  todo: Todo;
  isDragging: boolean;
  onAction: (todo: Todo, target: TodoStatus | 'delete') => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: todo.id });
  const overdue = isOverdue(todo);
  return (
    <article
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.3 : 1 }}
      className={`group p-3.5 border rounded-md bg-surface-1 hover:bg-surface-2 transition-colors cursor-grab active:cursor-grabbing ${
        overdue ? 'border-danger/40' : 'border-border'
      }`}
      {...attributes}
      {...listeners}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <CardContent todo={todo} overdue={overdue} />
        </div>
        <CardActions todo={todo} onAction={onAction} />
      </div>
    </article>
  );
}

function DroppableColumn({
  status,
  items,
  children,
  isDragging,
}: {
  status: TodoStatus;
  items: string[];
  children: React.ReactNode;
  isDragging: boolean;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: colId(status) });
  const highlight = isDragging && isOver;
  return (
    <SortableContext items={items} strategy={verticalListSortingStrategy}>
      <div
        ref={setNodeRef}
        className={`flex flex-col gap-2.5 min-h-[80px] rounded-md transition-colors duration-150 ${
          highlight ? 'bg-accent-solo/5 border-2 border-dashed border-accent-solo/30' : 'border-2 border-transparent'
        }`}
      >
        {children}
      </div>
    </SortableContext>
  );
}

// --- Main ---

export function Todos({ appName }: { appName: AppName }) {
  const { data, error, loading, reload } = useApi(() => api.todos(appName), [appName], { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS });
  const [priorityFilter, setPriorityFilter] = useState<string>('all');
  const [localItems, setLocalItems] = useState<Todo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showCancelled, setShowCancelled] = useState(false);
  const [pendingAction, setPendingAction] = useState<{ todo: Todo; action: 'delete' | 'cancel' } | null>(null);
  const { toast } = useToast();

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

  /** Resolve a DnD id (todo id or column id) to a TodoStatus. */
  const resolveColumn = useCallback(
    (id: string | number | undefined): TodoStatus | null => {
      if (id == null) return null;
      const colStatus = statusFromColId(String(id));
      if (colStatus) return colStatus;
      const item = localItems.find((t) => t.id === id);
      return item?.status ?? null;
    },
    [localItems],
  );

  const activeTodo = activeId ? localItems.find((t) => t.id === activeId) : null;

  // --- Drag handlers ---

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string);
  }

  function handleDragOver(event: DragOverEvent) {
    const { active, over } = event;
    if (!over) return;

    const activeCol = resolveColumn(active.id);
    const overCol = resolveColumn(over.id);
    if (!activeCol || !overCol || activeCol === overCol) return;

    // Live cross-column move: update status so card visually relocates
    setLocalItems((prev) => {
      const activeItem = prev.find((t) => t.id === active.id);
      if (!activeItem || activeItem.status === overCol) return prev;

      const next = prev.map((t) =>
        t.id === active.id ? { ...t, status: overCol } : t,
      );

      // Reorder: place active item adjacent to over item
      const overIdx = next.findIndex((t) => t.id === over.id);
      if (overIdx !== -1) {
        const activeIdx = next.findIndex((t) => t.id === active.id);
        const [moved] = next.splice(activeIdx, 1);
        const newOverIdx = next.findIndex((t) => t.id === over.id);
        next.splice(newOverIdx, 0, moved);
      }
      return next;
    });
  }

  async function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    const id = active.id as string;
    setActiveId(null);

    const original = data?.find((t) => t.id === id);
    if (!original) return;

    const targetCol = resolveColumn(over?.id);
    if (!targetCol || original.status === targetCol) return;

    // Status already changed via onDragOver; persist to server
    try {
      if (original.status === 'done' && targetCol !== 'done') {
        await api.reopenTodo(appName, id);
      } else if (targetCol === 'done') {
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

  // --- Action handler (button clicks) ---

  async function handleAction(todo: Todo, target: TodoStatus | 'delete') {
    if (target === 'delete') {
      setPendingAction({ todo, action: 'delete' });
      return;
    }
    if (target === 'cancelled') {
      setPendingAction({ todo, action: 'cancel' });
      return;
    }

    try {
      if (todo.status === 'done' && target !== 'done') {
        await api.reopenTodo(appName, todo.id);
      } else if (target === 'done') {
        await api.markTodoDone(appName, todo.id);
      } else if (target === 'in_progress') {
        await api.startTodo(appName, todo.id);
      } else {
        await api.revertTodo(appName, todo.id);
      }
      toast(`"${todo.title}" → ${columns.find((c) => c.key === target)?.label}`, 'success');
      reload();
    } catch {
      toast('Failed to update todo', 'error');
    }
  }

  async function confirmPendingAction() {
    if (!pendingAction) return;
    const { todo, action } = pendingAction;
    setPendingAction(null);
    try {
      if (action === 'delete') {
        await api.deleteTodo(appName, todo.id);
        toast(`"${todo.title}" deleted`, 'success');
      } else {
        await api.cancelTodo(appName, todo.id);
        toast(`"${todo.title}" cancelled`, 'success');
      }
      reload();
    } catch {
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
          {columnItems.cancelled.length > 0 && (
            <button
              type="button"
              onClick={() => setShowCancelled(!showCancelled)}
              className={`text-[12px] px-2 py-1 rounded-md border cursor-pointer transition-colors ${
                showCancelled
                  ? 'border-danger/40 bg-danger/10 text-danger'
                  : 'border-border bg-surface-2 text-text-muted hover:text-text-secondary'
              }`}
            >
              ✕ {columnItems.cancelled.length} cancelled
            </button>
          )}
          <span className="text-[11px] font-mono text-text-muted">{data.length} total</span>
        </div>
      </div>

      {showCancelled && columnItems.cancelled.length > 0 && (
        <div className="flex flex-wrap gap-2 px-1">
          {columnItems.cancelled.map((todo) => (
            <div
              key={todo.id}
              className="group flex items-center gap-2 px-3 py-1.5 rounded-md bg-surface-1 border border-border text-[12px] text-text-muted line-through"
            >
              <span className="truncate max-w-[200px]">{todo.title}</span>
              <button
                type="button"
                onClick={() => handleAction(todo, 'pending')}
                className="opacity-0 group-hover:opacity-100 text-[11px] text-text-muted hover:text-text no-underline bg-transparent border-0 cursor-pointer p-0 leading-none"
                title="Reopen"
              >
                ↩
              </button>
              <button
                type="button"
                onClick={() => handleAction(todo, 'delete')}
                className="opacity-0 group-hover:opacity-100 text-[11px] text-text-muted hover:text-danger no-underline bg-transparent border-0 cursor-pointer p-0 leading-none"
                title="Delete"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
      >
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {columns.filter((c) => c.key !== 'cancelled').map((col) => (
            <section key={col.key} className="space-y-2.5">
              <div className="flex items-center gap-2 mb-3">
                <span className={`w-2 h-2 rounded-full ${col.dot}`} />
                <span className="text-[12px] font-medium uppercase tracking-wider text-text-muted">{col.label}</span>
                <span className="ml-auto text-[11px] font-mono text-text-muted">
                  {columnItems[col.key].length}
                </span>
              </div>
              <DroppableColumn
                status={col.key}
                items={columnItems[col.key].map((t) => t.id)}
                isDragging={activeId !== null}
              >
                {columnItems[col.key].map((todo) => (
                  <SortableCard
                    key={todo.id}
                    todo={todo}
                    isDragging={activeId === todo.id}
                    onAction={handleAction}
                  />
                ))}
              </DroppableColumn>
            </section>
          ))}
        </div>

        <DragOverlay dropAnimation={{ duration: 200, easing: 'ease' }}>
          {activeTodo ? (
            <article className="p-3.5 border rounded-md bg-surface-2 border-border shadow-lg" style={{ cursor: 'grabbing' }}>
              <CardContent todo={activeTodo} overdue={isOverdue(activeTodo)} />
            </article>
          ) : null}
        </DragOverlay>
      </DndContext>
      <ConfirmDialog
        open={!!pendingAction}
        title={pendingAction?.action === 'delete' ? 'Delete Todo' : 'Cancel Todo'}
        description={
          pendingAction?.action === 'delete'
            ? `"${pendingAction.todo.title}" will be permanently deleted. This cannot be undone.`
            : `Cancel "${pendingAction?.todo.title}"?`
        }
        confirmLabel={pendingAction?.action === 'delete' ? 'Delete' : 'Cancel Todo'}
        danger={pendingAction?.action === 'delete'}
        onConfirm={confirmPendingAction}
        onCancel={() => setPendingAction(null)}
      />
    </div>
  );
}
