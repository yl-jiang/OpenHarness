import type { ReactNode } from "react";

/**
 * Right-side inspector panel (300-360px fixed width).
 * Used for AI suggestions, project properties, and context.
 */
export function RightInspector({
  title,
  children,
}: {
  title?: string;
  children: ReactNode;
}) {
  return (
    <aside className="w-[360px] shrink-0 border-l border-border bg-surface-1 overflow-y-auto hidden lg:block">
      {title && (
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-[13px] font-medium text-text-secondary m-0">{title}</h3>
        </div>
      )}
      <div className="p-4 space-y-4">{children}</div>
    </aside>
  );
}
