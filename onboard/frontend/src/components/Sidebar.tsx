import { NavLink } from 'react-router-dom';

import type { AppName } from '../api/types';
import { StatusBadge } from './StatusBadge';

const commonItems = [
  ['/', 'Dashboard'],
  ['/entries', 'Entries'],
  ['/records', 'Records'],
  ['/todos', 'Todos'],
  ['/reports', 'Reports'],
  ['/stats', 'Stats'],
  ['/search', 'Search'],
  ['/chat', 'Chat'],
  ['/settings', 'Settings'],
] as const;

const woloItems = [
  ['/decisions', 'Decisions'],
  ['/highlights', 'Highlights'],
] as const;

interface SidebarProps {
  appName: AppName;
  onAppChange: (appName: AppName) => void;
  gatewayStatus: string;
}

export function Sidebar({ appName, onAppChange, gatewayStatus }: SidebarProps) {
  const items = appName === 'wolo' ? [...commonItems, ...woloItems] : commonItems;
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">OH</div>
        <div>
          <strong>Onboard</strong>
          <span>{appName} dashboard</span>
        </div>
      </div>
      <div className="app-switch">
        <button className={appName === 'solo' ? 'active' : ''} onClick={() => onAppChange('solo')}>
          Solo
        </button>
        <button className={appName === 'wolo' ? 'active' : ''} onClick={() => onAppChange('wolo')}>
          Wolo
        </button>
      </div>
      <nav>
        {items.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === '/'}>
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <StatusBadge status={gatewayStatus} />
        <span>v0.1.0</span>
      </div>
    </aside>
  );
}
