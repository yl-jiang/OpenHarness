import type { ProjectStatus } from "../api/types";

const statusConfig: Record<ProjectStatus, { label: string; className: string }> = {
  active: { label: "Active", className: "bg-blue-500/15 text-blue-400" },
  completed: { label: "Completed", className: "bg-green-500/15 text-green-400" },
  archived: { label: "Archived", className: "bg-gray-500/15 text-gray-400" },
};

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  const cfg = statusConfig[status];
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${cfg.className}`}>
      {cfg.label}
    </span>
  );
}
