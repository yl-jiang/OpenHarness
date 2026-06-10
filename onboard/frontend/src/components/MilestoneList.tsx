import { useState } from "react";
import type { AppName, Milestone } from "../api/types";
import { api } from "../api/client";

interface Props {
  app: AppName;
  projectId: string;
  milestones: Milestone[];
  onChange: () => void;
}

export function MilestoneList({ app, projectId, milestones, onChange }: Props) {
  const [newTitle, setNewTitle] = useState("");
  const [newDate, setNewDate] = useState("");

  const handleAdd = async () => {
    if (!newTitle.trim()) return;
    await api.createMilestone(app, projectId, { title: newTitle.trim(), target_date: newDate });
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
    <div className="space-y-2">
      {milestones.map((m) => (
        <div key={m.id} className="flex items-center gap-3 px-3 py-2 rounded-md bg-surface-2">
          <button
            onClick={() => m.status === "pending" && handleComplete(m.id)}
            className={`w-4 h-4 rounded-full border-2 flex-shrink-0 cursor-pointer ${
              m.status === "completed"
                ? "bg-green-500 border-green-500"
                : "border-border hover:border-accent-solo"
            }`}
            disabled={m.status === "completed"}
          />
          <div className="flex-1 min-w-0">
            <span className={`text-sm ${m.status === "completed" ? "line-through text-text-muted" : "text-text"}`}>
              {m.title}
            </span>
            {m.target_date && (
              <span className="ml-2 text-[11px] text-text-muted">{m.target_date}</span>
            )}
          </div>
          <button
            onClick={() => handleDelete(m.id)}
            className="text-text-muted hover:text-red-400 text-xs cursor-pointer bg-transparent border-0"
          >
            Delete
          </button>
        </div>
      ))}
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
