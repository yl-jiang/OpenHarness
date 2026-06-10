import { Link } from "react-router-dom";
import type { Project } from "../api/types";
import { ProjectStatusBadge, RiskBadge } from "./ProjectStatusBadge";
import { ProjectCompletionBar } from "./ProjectCompletionBar";

export function ProjectCard({ project }: { project: Project }) {
  return (
    <Link
      to={`/projects/${project.id}`}
      className="block rounded-lg border border-border bg-surface-2 p-4 hover:bg-surface-3 transition-colors no-underline"
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <h3 className="text-sm font-medium text-text truncate m-0">{project.title}</h3>
        <ProjectStatusBadge status={project.status} />
      </div>
      {project.description && (
        <p className="text-xs text-text-muted mb-3 line-clamp-2 m-0">{project.description}</p>
      )}
      <ProjectCompletionBar pct={project.completion_pct} />
      <div className="flex items-center gap-3 mt-3 text-[11px] text-text-muted">
        <span>{project.completed_milestone_count}/{project.milestone_count} milestones</span>
        {project.target_date && <span>{project.target_date}</span>}
        <RiskBadge status={project.risk_status} />
      </div>
    </Link>
  );
}
