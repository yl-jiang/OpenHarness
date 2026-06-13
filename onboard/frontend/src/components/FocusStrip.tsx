import { useState } from "react";

export interface FocusItem {
  label: string;
  value: string | number;
  color?: string;
}

/**
 * Compact horizontal metrics strip. Replaces rows of StatsCards.
 * Renders as a subtle band with inline metrics, no card borders.
 */
export function FocusStrip({ items }: { items: FocusItem[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border py-3 px-1">
      {items.map((item, i) => (
        <div key={i} className="flex items-center gap-1.5 text-[12px]">
          <span className="text-text-muted">{item.label}</span>
          <span
            className="font-medium tabular-nums"
            style={item.color ? { color: item.color } : undefined}
          >
            {item.value}
          </span>
        </div>
      ))}
    </div>
  );
}
