import { Link } from "react-router-dom";
import type { Project } from "../api/types";
import { ProjectHealthPill } from "./ProjectHealthPill";
import { ProjectCompletionBar } from "./ProjectCompletionBar";

/**
 * Compact project row card for dense lists and kanban columns.
 * Replaces the heavier ProjectCard component.
 */
export function ProjectRowCard({ project }: { project: Project }) {
  const daysAgo = project.last_activity_at
    ? Math.floor(
        (Date.now() - new Date(project.last_activity_at).getTime()) / 86400000
      )
    : null;

  return (
    <Link
      to={`/projects/${project.id}`}
      className="flex items-center gap-3 px-3 py-2.5 rounded-md hover:bg-surface-2 transition-colors no-underline text-text border-b border-border-subtle last:border-0"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-medium truncate">{project.title}</span>
          <ProjectHealthPill status={project.risk_status} />
        </div>
        {project.description && (
          <p className="text-[11px] text-text-muted mt-0.5 truncate m-0">
            {project.description}
          </p>
        )}
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <ProjectCompletionBar pct={project.completion_pct} />
        {project.milestone_count > 0 && (
          <span className="text-[11px] text-text-muted tabular-nums whitespace-nowrap">
            {project.completed_milestone_count}/{project.milestone_count}
          </span>
        )}
        <span className="text-[11px] text-text-muted tabular-nums w-12 text-right">
          {daysAgo !== null ? (daysAgo === 0 ? "today" : `${daysAgo}d`) : "—"}
        </span>
      </div>
    </Link>
  );
}
