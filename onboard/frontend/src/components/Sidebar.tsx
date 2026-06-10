import { NavLink } from 'react-router-dom';

import type { AppName } from '../api/types';
import { StatusBadge } from './StatusBadge';

const commonItems = [
  ['/', '◇', 'Dashboard'],
  ['/entries', '⊞', 'Entries'],
  ['/records', '◈', 'Records'],
  ['/todos', '☐', 'Todos'],
  ['/projects', '▦', 'Projects'],
  ['/reports', '▤', 'Reports'],
  ['/feeds', '◎', 'Feed Digests'],
  ['/stats', '⊿', 'Stats'],
  ['/search', '⌕', 'Search'],
  ['/chat', '⊙', 'Chat'],
] as const;

const woloItems = [
  ['/decisions', '⧫', 'Decisions'],
  ['/highlights', '◉', 'Highlights'],
] as const;

interface SidebarProps {
  appName: AppName;
  onAppChange: (appName: AppName) => void;
  gatewayStatus: string;
}

export function Sidebar({ appName, onAppChange, gatewayStatus }: SidebarProps) {
  const items = appName === 'wolo' ? [...commonItems, ...woloItems] : commonItems;
  const accent = appName === 'solo' ? 'text-accent-solo' : 'text-accent-wolo';

  return (
    <aside className="sticky top-0 flex flex-col h-screen border-r border-border bg-surface-1 overflow-y-auto">
      {/* Brand */}
      <div className="px-5 pt-5 pb-3">
        <div className="flex items-center gap-2.5">
          <span className={`font-serif text-2xl ${accent}`}>O</span>
          <div>
            <div className="text-sm font-medium text-text">Onboard</div>
            <div className="text-[11px] text-text-muted font-mono">{appName}</div>
          </div>
        </div>
      </div>

      {/* App switch */}
      <div className="mx-4 mb-4 grid grid-cols-2 gap-0.5 p-0.5 rounded-md bg-surface-2">
        <button
          className={`text-xs py-1.5 px-2 rounded-[var(--radius-sm)] font-medium transition-colors cursor-pointer border-0 ${
            appName === 'solo' ? 'bg-surface-3 text-text' : 'bg-transparent text-text-muted hover:text-text-secondary'
          }`}
          onClick={() => onAppChange('solo')}
        >
          Solo
        </button>
        <button
          className={`text-xs py-1.5 px-2 rounded-[var(--radius-sm)] font-medium transition-colors cursor-pointer border-0 ${
            appName === 'wolo' ? 'bg-surface-3 text-text' : 'bg-transparent text-text-muted hover:text-text-secondary'
          }`}
          onClick={() => onAppChange('wolo')}
        >
          Wolo
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 flex flex-col gap-0.5">
        {items.map(([to, icon, label]) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-3 py-2 rounded-md text-[13px] no-underline transition-colors active:scale-[0.97] ${
                isActive
                  ? `${accent} bg-surface-2 font-medium`
                  : 'text-text-secondary hover:text-text hover:bg-surface-2'
              }`
            }
          >
            <span className="text-sm w-5 text-center opacity-70">{icon}</span>
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-border flex items-center justify-between">
        <StatusBadge status={gatewayStatus} />
        <div className="flex items-center gap-2">
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              `p-1.5 rounded-md transition-colors ${isActive ? `${accent}` : 'text-text-muted hover:text-text'}`
            }
            title="Settings"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12.22 2h-.44a2 2 0 00-2 2v.18a2 2 0 01-1 1.73l-.43.25a2 2 0 01-2 0l-.15-.08a2 2 0 00-2.73.73l-.22.38a2 2 0 00.73 2.73l.15.1a2 2 0 011 1.72v.51a2 2 0 01-1 1.74l-.15.09a2 2 0 00-.73 2.73l.22.38a2 2 0 002.73.73l.15-.08a2 2 0 012 0l.43.25a2 2 0 011 1.73V20a2 2 0 002 2h.44a2 2 0 002-2v-.18a2 2 0 011-1.73l.43-.25a2 2 0 012 0l.15.08a2 2 0 002.73-.73l.22-.39a2 2 0 00-.73-2.73l-.15-.08a2 2 0 01-1-1.74v-.5a2 2 0 011-1.74l.15-.09a2 2 0 00.73-2.73l-.22-.38a2 2 0 00-2.73-.73l-.15.08a2 2 0 01-2 0l-.43-.25a2 2 0 01-1-1.73V4a2 2 0 00-2-2z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
          </NavLink>
          <span className="text-[11px] font-mono text-text-muted">v0.1</span>
        </div>
      </div>
    </aside>
  );
}
