import type { ProjectStatus, ProjectRiskStatus } from "../api/types";

const statusConfig: Record<ProjectStatus, { label: string; className: string }> = {
  active: { label: "Active", className: "bg-blue-500/15 text-blue-400" },
  completed: { label: "Completed", className: "bg-green-500/15 text-green-400" },
  archived: { label: "Archived", className: "bg-gray-500/15 text-gray-400" },
};

const riskConfig: Record<ProjectRiskStatus, { label: string; className: string }> = {
  normal: { label: "Normal", className: "bg-green-500/15 text-green-400" },
  attention: { label: "Attention", className: "bg-yellow-500/15 text-yellow-400" },
  at_risk: { label: "At Risk", className: "bg-red-500/15 text-red-400" },
};

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  const cfg = statusConfig[status];
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${cfg.className}`}>
      {cfg.label}
    </span>
  );
}

export function RiskBadge({ status }: { status: ProjectRiskStatus }) {
  const cfg = riskConfig[status];
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${cfg.className}`}>
      {cfg.label}
    </span>
  );
}
