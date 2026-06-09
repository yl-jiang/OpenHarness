import { Link } from 'react-router-dom';

export interface BreadcrumbItem {
  label: string;
  to?: string;
}

interface BreadcrumbProps {
  items: BreadcrumbItem[];
}

export function Breadcrumb({ items }: BreadcrumbProps) {
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-[12px] font-mono text-text-muted mb-4">
      {items.map((item, i) => {
        const isLast = i === items.length - 1;
        const separator = i > 0 ? (
          <span key={`sep-${i}`} className="text-border select-none" aria-hidden="true">/</span>
        ) : null;
        if (isLast || !item.to) {
          return (
            <span key={i} className="inline-flex items-center gap-1.5">
              {separator}
              <span className={isLast ? 'text-text-secondary' : ''}>{item.label}</span>
            </span>
          );
        }
        return (
          <span key={i} className="inline-flex items-center gap-1.5">
            {separator}
            <Link to={item.to} className="text-text-muted hover:text-text no-underline transition-colors">
              {item.label}
            </Link>
          </span>
        );
      })}
    </nav>
  );
}
