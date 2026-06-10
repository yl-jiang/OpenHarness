import { useCallback, useEffect, useState } from "react";
import type { AppName, Project } from "../api/types";
import { api } from "../api/client";
import { ProjectCard } from "../components/ProjectCard";
import { EmptyState } from "../components/EmptyState";

export function Projects({ appName }: { appName: AppName }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"kanban" | "list">("kanban");
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newDate, setNewDate] = useState("");
  const [newPriority, setNewPriority] = useState("medium");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.projects(appName, { status: "all" });
      setProjects(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [appName]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    try {
      await api.createProject(appName, {
        title: newTitle.trim(),
        description: newDesc,
        target_date: newDate,
        priority: newPriority,
      });
      setShowCreate(false);
      setNewTitle("");
      setNewDesc("");
      setNewDate("");
      setNewPriority("medium");
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  const active = projects.filter((p) => p.status === "active");
  const completed = projects.filter((p) => p.status === "completed");
  const archived = projects.filter((p) => p.status === "archived");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text m-0">Projects</h1>
        <div className="flex items-center gap-3">
          <div className="flex gap-0.5 p-0.5 rounded-md bg-surface-2">
            <button
              onClick={() => setView("kanban")}
              className={`text-xs py-1 px-2.5 rounded-[var(--radius-sm)] cursor-pointer border-0 ${view === "kanban" ? "bg-surface-3 text-text" : "bg-transparent text-text-muted"}`}
            >
              Kanban
            </button>
            <button
              onClick={() => setView("list")}
              className={`text-xs py-1 px-2.5 rounded-[var(--radius-sm)] cursor-pointer border-0 ${view === "list" ? "bg-surface-3 text-text" : "bg-transparent text-text-muted"}`}
            >
              List
            </button>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="px-3 py-1.5 rounded-md bg-accent-solo/20 text-accent-solo text-sm hover:bg-accent-solo/30 cursor-pointer border-0"
          >
            + New Project
          </button>
        </div>
      </div>

      {error && <div className="text-sm text-red-400">{error}</div>}

      {showCreate && (
        <div className="rounded-lg border border-border bg-surface-2 p-4 space-y-3">
          <input
            type="text"
            placeholder="Project title"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            className="w-full px-3 py-2 rounded-md bg-surface-1 border border-border text-sm text-text placeholder-text-muted"
            autoFocus
          />
          <input
            type="text"
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            className="w-full px-3 py-2 rounded-md bg-surface-1 border border-border text-sm text-text placeholder-text-muted"
          />
          <div className="flex gap-3">
            <input
              type="date"
              value={newDate}
              onChange={(e) => setNewDate(e.target.value)}
              className="px-3 py-2 rounded-md bg-surface-1 border border-border text-sm text-text"
            />
            <select
              value={newPriority}
              onChange={(e) => setNewPriority(e.target.value)}
              className="px-3 py-2 rounded-md bg-surface-1 border border-border text-sm text-text"
            >
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
          <div className="flex gap-2">
            <button onClick={handleCreate} className="px-4 py-1.5 rounded-md bg-accent-solo/20 text-accent-solo text-sm cursor-pointer border-0">
              Create
            </button>
            <button onClick={() => setShowCreate(false)} className="px-4 py-1.5 rounded-md bg-surface-3 text-text-secondary text-sm cursor-pointer border-0">
              Cancel
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="h-40 rounded-lg bg-surface-2 animate-pulse" />
      ) : projects.length === 0 ? (
        <EmptyState
          title="No projects yet"
          description="Projects can start from just a title. Create your first project to begin tracking milestones and linking records."
        />
      ) : view === "kanban" ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div>
            <h2 className="text-sm font-medium text-text-secondary mb-3">Active ({active.length})</h2>
            <div className="space-y-3">
              {active.map((p) => <ProjectCard key={p.id} project={p} />)}
            </div>
          </div>
          <div>
            <h2 className="text-sm font-medium text-text-secondary mb-3">Completed ({completed.length})</h2>
            <div className="space-y-3">
              {completed.map((p) => <ProjectCard key={p.id} project={p} />)}
            </div>
          </div>
          <div>
            <h2 className="text-sm font-medium text-text-secondary mb-3">Archived ({archived.length})</h2>
            <div className="space-y-3">
              {archived.map((p) => <ProjectCard key={p.id} project={p} />)}
            </div>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-2">
                <th className="text-left px-4 py-2 text-text-secondary font-medium">Project</th>
                <th className="text-left px-4 py-2 text-text-secondary font-medium">Status</th>
                <th className="text-left px-4 py-2 text-text-secondary font-medium">Progress</th>
                <th className="text-left px-4 py-2 text-text-secondary font-medium">Milestones</th>
                <th className="text-left px-4 py-2 text-text-secondary font-medium">Target</th>
                <th className="text-left px-4 py-2 text-text-secondary font-medium">Risk</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.id} className="border-b border-border hover:bg-surface-2">
                  <td className="px-4 py-2">
                    <a href={`/projects/${p.id}`} className="text-text hover:text-accent-solo no-underline">{p.title}</a>
                  </td>
                  <td className="px-4 py-2 text-text-muted capitalize">{p.status}</td>
                  <td className="px-4 py-2 text-text-muted">{p.completion_pct !== null ? `${p.completion_pct}%` : "-"}</td>
                  <td className="px-4 py-2 text-text-muted">{p.completed_milestone_count}/{p.milestone_count}</td>
                  <td className="px-4 py-2 text-text-muted">{p.target_date || "-"}</td>
                  <td className="px-4 py-2 text-text-muted capitalize">{p.risk_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
