export interface SegmentedOption {
  label: string;
  value: string;
}

/**
 * Compact segmented control (pill-style tabs).
 * Replaces radio groups and toggle buttons for view switching.
 */
export function SegmentedControl({
  options,
  value,
  onChange,
}: {
  options: SegmentedOption[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="inline-flex rounded-md bg-surface-2 p-0.5 text-[12px]">
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`px-3 py-1 rounded cursor-pointer border-0 transition-colors ${
            value === opt.value
              ? "bg-surface-3 text-text font-medium"
              : "bg-transparent text-text-muted hover:text-text-secondary"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
