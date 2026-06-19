import { useState, useCallback } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragStartEvent,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { AppName, Milestone } from "../api/types";
import { api } from "../api/client";

interface Props {
  app: AppName;
  projectId: string;
  milestones: Milestone[];
  onChange: () => void;
}

/* ── Sortable Milestone Item (in-list) ────────────────────────── */

function SortableMilestoneItem({
  milestone,
  isDragging,
  onComplete,
  onDelete,
}: {
  milestone: Milestone;
  isDragging: boolean;
  onComplete: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
  } = useSortable({ id: milestone.id, animateLayoutChanges: () => false });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    /* Placeholder: dimmed empty slot while dragging this item away */
    opacity: isDragging ? 0.3 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`group flex items-center gap-3 px-3 py-2 rounded-md bg-surface-2 transition-[background-color,box-shadow] duration-150 ${
        isDragging ? "border border-dashed border-border" : "hover:bg-surface-2/80"
      }`}
    >
      {/* Drag handle */}
      <span
        className="text-text-muted text-[10px] cursor-grab active:cursor-grabbing select-none shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
        {...attributes}
        {...listeners}
      >
        ⠿
      </span>

      {/* Completion circle */}
      <button
        onClick={() => milestone.status === "pending" && onComplete(milestone.id)}
        className={`w-4 h-4 rounded-full border-2 flex-shrink-0 cursor-pointer ${
          milestone.status === "completed"
            ? "bg-green-500 border-green-500"
            : "border-border hover:border-accent-solo"
        }`}
        disabled={milestone.status === "completed"}
      />

      {/* Title + date */}
      <div className="flex-1 min-w-0">
        <span
          className={`text-sm ${
            milestone.status === "completed"
              ? "line-through text-text-muted"
              : "text-text"
          }`}
        >
          {milestone.title}
        </span>
        {milestone.target_date && (
          <span className="ml-2 text-[11px] text-text-muted">
            {milestone.target_date}
          </span>
        )}
      </div>

      {/* Trash icon */}
      <button
        onClick={() => onDelete(milestone.id)}
        className="text-text-muted hover:text-red-400 cursor-pointer bg-transparent border-0 p-0 opacity-0 group-hover:opacity-100 transition-opacity"
        title="Delete milestone"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="3 6 5 6 21 6" />
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          <line x1="10" y1="11" x2="10" y2="17" />
          <line x1="14" y1="11" x2="14" y2="17" />
        </svg>
      </button>
    </div>
  );
}

/* ── Drag Overlay Card (follows cursor) ───────────────────────── */

function MilestoneOverlayCard({ milestone }: { milestone: Milestone }) {
  return (
    <div className="flex items-center gap-3 px-3 py-2 rounded-md bg-surface-2 shadow-lg ring-1 ring-accent-solo/30" style={{ scale: "0.96", cursor: "grabbing" }}>
      <span className="text-text-muted text-[10px] select-none shrink-0">⠿</span>
      <button
        className={`w-4 h-4 rounded-full border-2 flex-shrink-0 ${
          milestone.status === "completed"
            ? "bg-green-500 border-green-500"
            : "border-border"
        }`}
      />
      <div className="flex-1 min-w-0">
        <span
          className={`text-sm ${
            milestone.status === "completed"
              ? "line-through text-text-muted"
              : "text-text"
          }`}
        >
          {milestone.title}
        </span>
        {milestone.target_date && (
          <span className="ml-2 text-[11px] text-text-muted">
            {milestone.target_date}
          </span>
        )}
      </div>
    </div>
  );
}

/* ── Main MilestoneList ───────────────────────────────────────── */

export function MilestoneList({ app, projectId, milestones, onChange }: Props) {
  const [newTitle, setNewTitle] = useState("");
  const [newDate, setNewDate] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [localItems, setLocalItems] = useState<Milestone[]>(milestones);

  /* Sync local state when parent data changes */
  if (milestones !== localItems && !activeId) {
    setLocalItems(milestones);
  }

  const activeMilestone = activeId ? localItems.find((m) => m.id === activeId) : null;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveId(event.active.id as string);
  }, []);

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      const { active, over } = event;
      setActiveId(null);

      if (!over || active.id === over.id) return;

      const oldIndex = localItems.findIndex((m) => m.id === active.id);
      const newIndex = localItems.findIndex((m) => m.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return;

      /* Optimistic reorder */
      const reordered = [...localItems];
      const [moved] = reordered.splice(oldIndex, 1);
      reordered.splice(newIndex, 0, moved);
      setLocalItems(reordered);

      try {
        await api.reorderMilestones(
          app,
          projectId,
          reordered.map((m) => m.id),
        );
      } catch {
        /* Revert on failure */
        setLocalItems(milestones);
      }
      onChange();
    },
    [app, projectId, localItems, milestones, onChange],
  );

  const handleAdd = async () => {
    if (!newTitle.trim()) return;
    await api.createMilestone(app, projectId, {
      title: newTitle.trim(),
      target_date: newDate,
    });
    setNewTitle("");
    setNewDate("");
    onChange();
  };

  const handleComplete = async (id: string) => {
    await api.completeMilestone(app, id);
    onChange();
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this milestone?")) return;
    await api.deleteMilestone(app, id);
    onChange();
  };

  return (
    <div className="space-y-1.5">
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <SortableContext
          items={localItems.map((m) => m.id)}
          strategy={verticalListSortingStrategy}
        >
          {localItems.map((m) => (
            <SortableMilestoneItem
              key={m.id}
              milestone={m}
              isDragging={activeId === m.id}
              onComplete={handleComplete}
              onDelete={handleDelete}
            />
          ))}
        </SortableContext>

        <DragOverlay dropAnimation={null}>
          {activeMilestone ? (
            <MilestoneOverlayCard milestone={activeMilestone} />
          ) : null}
        </DragOverlay>
      </DndContext>

      {/* Add milestone form */}
      <div className="flex items-center gap-2 pt-2">
        <input
          type="text"
          placeholder="New milestone..."
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          className="flex-1 px-3 py-1.5 rounded-md bg-surface-2 border border-border text-sm text-text placeholder-text-muted"
        />
        <input
          type="date"
          value={newDate}
          onChange={(e) => setNewDate(e.target.value)}
          className="px-2 py-1.5 rounded-md bg-surface-2 border border-border text-xs text-text"
        />
        <button
          onClick={handleAdd}
          className="px-3 py-1.5 rounded-md bg-accent-solo/20 text-accent-solo text-sm hover:bg-accent-solo/30 cursor-pointer border-0"
        >
          Add
        </button>
      </div>
    </div>
  );
}
