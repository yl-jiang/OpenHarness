import type { ProjectRiskStatus } from "../api/types";

const HEALTH_MAP: Record<string, { label: string; color: string; bg: string }> = {
  at_risk: { label: "At Risk", color: "#f87171", bg: "rgba(248,113,113,0.12)" },
  attention: { label: "Attention", color: "#fbbf24", bg: "rgba(251,191,36,0.12)" },
  normal: { label: "Normal", color: "#34d399", bg: "rgba(52,211,153,0.12)" },
};

/**
 * Compact health status pill. Fixed width, stable layout.
 */
export function ProjectHealthPill({ status }: { status: ProjectRiskStatus }) {
  const config = HEALTH_MAP[status] ?? HEALTH_MAP.normal;
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium whitespace-nowrap"
      style={{ color: config.color, backgroundColor: config.bg }}
    >
      {config.label}
    </span>
  );
}
