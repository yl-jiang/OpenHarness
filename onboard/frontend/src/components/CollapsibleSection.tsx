import { useState } from "react";
import type { ReactNode } from "react";

/**
 * Collapsible section with a subtle border-top separator.
 * Replaces heavy card wrappers for secondary content areas.
 */
export function CollapsibleSection({
  title,
  children,
  defaultOpen = true,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-border pt-4">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full bg-transparent border-0 cursor-pointer py-1 mb-2 group"
      >
        <span
          className={`text-text-muted text-[10px] transition-transform ${
            open ? "rotate-90" : ""
          }`}
        >
          &#9654;
        </span>
        <span className="text-[13px] font-medium text-text-secondary group-hover:text-text">
          {title}
        </span>
      </button>
      {open && children}
    </div>
  );
}
