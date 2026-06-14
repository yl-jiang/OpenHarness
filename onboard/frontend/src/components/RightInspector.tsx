import type { ReactNode } from "react";

/**
 * Right-side inspector panel (300-360px fixed width).
 * Uses card-grouped sections with warm accent header line.
 */
export function RightInspector({
  title,
  children,
}: {
  title?: string;
  children: ReactNode;
}) {
  return (
    <aside className="w-[320px] shrink-0 border-l border-border-subtle bg-surface-1 overflow-y-auto hidden lg:block">
      {title && (
        <div className="relative px-4 pt-4 pb-3">
          {/* Warm accent gradient line at the top */}
          <div
            className="absolute top-0 left-0 right-0 h-px"
            style={{
              background:
                "linear-gradient(90deg, transparent 0%, var(--color-accent-solo) 30%, var(--color-accent-solo) 70%, transparent 100%)",
              opacity: 0.4,
            }}
          />
          <h3 className="text-[11px] font-semibold text-text-muted m-0 uppercase tracking-[0.08em]">
            {title}
          </h3>
        </div>
      )}
      <div className="px-3 pb-4 space-y-2.5">{children}</div>
    </aside>
  );
}

/**
 * A grouped card inside the inspector — subtle border, rounded, hover glow.
 */
export function InspectorCard({
  label,
  icon,
  children,
  accent,
  className = "",
}: {
  label?: string;
  icon?: string;
  children: ReactNode;
  accent?: boolean;
  className?: string;
}) {
  return (
    <div
      className={`
        rounded-lg border transition-all duration-200
        ${accent
          ? "border-accent-solo/20 bg-accent-solo-dim/30 hover:border-accent-solo/35"
          : "border-border-subtle bg-surface-2 hover:border-border"
        }
        ${className}
      `}
    >
      {label && (
        <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
          {icon && <span className="text-[10px] opacity-60">{icon}</span>}
          <span className="text-[10px] font-medium uppercase tracking-[0.06em] text-text-muted">
            {label}
          </span>
        </div>
      )}
      <div className={`px-3 pb-2.5 ${label ? "" : "pt-2.5"}`}>{children}</div>
    </div>
  );
}

/**
 * Key-value row inside an InspectorCard.
 */
export function InspectorRow({
  label,
  value,
  muted,
}: {
  label: string;
  value: React.ReactNode;
  muted?: boolean;
}) {
  return (
    <div className="flex justify-between items-baseline py-[3px]">
      <span className="text-[11px] text-text-muted">{label}</span>
      <span
        className={`text-[11px] tabular-nums ${muted ? "text-text-muted" : "text-text-secondary"}`}
      >
        {value}
      </span>
    </div>
  );
}
