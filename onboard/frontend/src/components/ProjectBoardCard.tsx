import { Link } from "react-router-dom";
import type { Project } from "../api/types";
import { ProjectHealthPill } from "./ProjectHealthPill";
import { ProjectCompletionBar } from "./ProjectCompletionBar";

const PRIORITY_DOT: Record<string, string> = {
  high: "#f87171",
  medium: "#fbbf24",
  low: "#34d399",
};

/**
 * Vertical card for board / kanban columns.
 */
export function ProjectBoardCard({ project }: { project: Project }) {
  const daysAgo = project.last_activity_at
    ? Math.floor(
        (Date.now() - new Date(project.last_activity_at).getTime()) / 86400000,
      )
    : null;

  const dotColor = PRIORITY_DOT[project.priority] ?? PRIORITY_DOT.medium;

  return (
    <Link
      to={`/projects/${project.id}`}
      className="block rounded-lg border border-border bg-surface-1 p-3 hover:bg-surface-2 transition-colors no-underline text-text mb-2 last:mb-0"
    >
      {/* Title row */}
      <div className="flex items-start gap-2 mb-1.5">
        <span
          className="mt-1 h-2 w-2 rounded-full shrink-0"
          style={{ backgroundColor: dotColor }}
          title={project.priority}
        />
        <span className="text-[13px] font-medium leading-snug flex-1 min-w-0 line-clamp-2">
          {project.title}
        </span>
        <ProjectHealthPill status={project.risk_status} />
      </div>

      {/* Description */}
      {project.description && (
        <p className="text-[11px] text-text-muted m-0 mb-2.5 line-clamp-2 leading-relaxed">
          {project.description}
        </p>
      )}

      {/* Progress */}
      <div className="mb-2">
        <ProjectCompletionBar pct={project.completion_pct} />
      </div>

      {/* Footer meta */}
      <div className="flex items-center gap-3 text-[11px] text-text-muted">
        {project.milestone_count > 0 && (
          <span className="tabular-nums">
            {project.completed_milestone_count}/{project.milestone_count} ms
          </span>
        )}
        {project.target_date && (
          <span>{project.target_date}</span>
        )}
        <span className="ml-auto tabular-nums">
          {daysAgo !== null ? (daysAgo === 0 ? "today" : `${daysAgo}d ago`) : "\u2014"}
        </span>
      </div>
    </Link>
  );
}
