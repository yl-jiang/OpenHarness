import type { ReactNode } from 'react';

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-6 text-center">
      {icon ? (
        <div className="mb-4 w-12 h-12 grid place-items-center rounded-lg bg-surface-2 text-text-muted text-xl">
          {icon}
        </div>
      ) : null}
      <h3 className="text-sm font-medium text-text m-0">{title}</h3>
      {description ? (
        <p className="mt-1.5 text-[13px] text-text-muted m-0 max-w-xs leading-relaxed">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
