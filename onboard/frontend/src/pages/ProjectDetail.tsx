import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import type { AppName, Project, Milestone, ProjectLink } from "../api/types";
import { api } from "../api/client";
import { ProjectStatusBadge, RiskBadge } from "../components/ProjectStatusBadge";
import { ProjectCompletionBar } from "../components/ProjectCompletionBar";
import { MilestoneList } from "../components/MilestoneList";

export function ProjectDetail({ appName }: { appName: AppName }) {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [links, setLinks] = useState<ProjectLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const [p, m, l] = await Promise.all([
        api.project(appName, id),
        api.milestones(appName, id),
        api.projectLinks(appName, id),
      ]);
      setProject(p);
      setMilestones(m);
      setLinks(l);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [appName, id]);

  useEffect(() => { load(); }, [load]);

  const handleAction = async (action: string) => {
    if (!project || !id) return;
    try {
      switch (action) {
        case "complete":
          await api.completeProject(appName, id);
          break;
        case "archive":
          await api.archiveProject(appName, id);
          break;
        case "reactivate":
          await api.reactivateProject(appName, id);
          break;
        case "delete":
          if (!confirm("Delete this project? Linked records will NOT be deleted.")) return;
          await api.deleteProject(appName, id);
          window.location.href = "/projects";
          return;
      }
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  if (loading) return <div className="h-40 rounded-lg bg-surface-2 animate-pulse" />;
  if (!project) return <div className="text-text-muted">Project not found</div>;

  return (
    <div className="space-y-6">
      <Link to="/projects" className="text-sm text-text-muted hover:text-text no-underline">
        &larr; Back to Projects
      </Link>

      {error && <div className="text-sm text-red-400">{error}</div>}

      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-text m-0">{project.title}</h1>
            <ProjectStatusBadge status={project.status} />
            <RiskBadge status={project.risk_status} />
          </div>
          {project.description && (
            <p className="mt-1 text-sm text-text-secondary m-0">{project.description}</p>
          )}
        </div>
        <div className="flex gap-2">
          {project.status === "active" && (
            <>
              <button onClick={() => handleAction("complete")} className="px-3 py-1.5 rounded-md bg-green-500/20 text-green-400 text-xs cursor-pointer border-0">Complete</button>
              <button onClick={() => handleAction("archive")} className="px-3 py-1.5 rounded-md bg-gray-500/20 text-gray-400 text-xs cursor-pointer border-0">Archive</button>
            </>
          )}
          {(project.status === "completed" || project.status === "archived") && (
            <button onClick={() => handleAction("reactivate")} className="px-3 py-1.5 rounded-md bg-blue-500/20 text-blue-400 text-xs cursor-pointer border-0">Reactivate</button>
          )}
          <button onClick={() => handleAction("delete")} className="px-3 py-1.5 rounded-md bg-red-500/20 text-red-400 text-xs cursor-pointer border-0">Delete</button>
        </div>
      </div>

      {/* Progress */}
      <div className="rounded-lg border border-border bg-surface-2 p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-text-secondary">Progress</span>
          <span className="text-xs text-text-muted">Source: {project.completion_source}</span>
        </div>
        <ProjectCompletionBar pct={project.completion_pct} />
        <div className="flex gap-6 mt-3 text-xs text-text-muted">
          <span>Milestones: {project.completed_milestone_count}/{project.milestone_count}</span>
          <span>Records: {project.linked_record_count}</span>
          <span>Todos: {project.completed_linked_todo_count}/{project.linked_todo_count}</span>
          {project.target_date && <span>Target: {project.target_date}</span>}
        </div>
      </div>

      {/* Milestones */}
      <div>
        <h2 className="text-sm font-medium text-text-secondary mb-3">Milestones</h2>
        <MilestoneList app={appName} projectId={project.id} milestones={milestones} onChange={load} />
      </div>

      {/* Linked Entities */}
      <div>
        <h2 className="text-sm font-medium text-text-secondary mb-3">Linked Entities ({links.length})</h2>
        {links.length === 0 ? (
          <p className="text-xs text-text-muted">No entities linked yet.</p>
        ) : (
          <div className="space-y-1">
            {links.map((l) => (
              <div key={l.id} className="flex items-center justify-between px-3 py-2 rounded-md bg-surface-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="px-1.5 py-0.5 rounded bg-surface-3 text-text-muted">{l.entity_type}</span>
                  <span className="text-text font-mono">{l.entity_id.slice(0, 8)}</span>
                  <span className="text-text-muted capitalize">({l.status})</span>
                </div>
                <div className="flex gap-2">
                  {l.status === "pending" && (
                    <>
                      <button onClick={() => { api.acceptProjectLink(appName, l.id).then(load); }} className="text-green-400 cursor-pointer bg-transparent border-0">Accept</button>
                      <button onClick={() => { api.rejectProjectLink(appName, l.id).then(load); }} className="text-red-400 cursor-pointer bg-transparent border-0">Reject</button>
                    </>
                  )}
                  <button onClick={() => { api.deleteProjectLink(appName, l.id).then(load); }} className="text-text-muted hover:text-red-400 cursor-pointer bg-transparent border-0">Unlink</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
