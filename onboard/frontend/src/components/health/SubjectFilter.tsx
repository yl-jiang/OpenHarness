interface SubjectFilterProps {
  subjects: Record<string, number>;
  selected: string | null;
  onSelect: (subject: string | null) => void;
}

const LABEL_MAP: Record<string, string> = {
  self: '自己',
};

export function SubjectFilter({ subjects, selected, onSelect }: SubjectFilterProps) {
  const entries = Object.entries(subjects);
  if (entries.length === 0) return null;

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {entries.map(([name, count]) => {
        const label = LABEL_MAP[name] ?? name;
        const isActive = selected === name;
        return (
          <button
            key={name}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer border-0 ${
              isActive ? 'bg-surface-3 text-text' : 'bg-transparent text-text-muted hover:text-text-secondary'
            }`}
            onClick={() => onSelect(name)}
          >
            {label} ({count})
          </button>
        );
      })}
    </div>
  );
}
