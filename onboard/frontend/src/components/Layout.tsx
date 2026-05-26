import { Outlet, useNavigate, useLocation } from 'react-router-dom';

import type { AppName } from '../api/types';
import { SearchBar } from './SearchBar';
import { Sidebar } from './Sidebar';
import { StatusBadge } from './StatusBadge';

interface LayoutProps {
  appName: AppName;
  gatewayStatus: string;
  onAppChange: (appName: AppName) => void;
}

export function Layout({ appName, gatewayStatus, onAppChange }: LayoutProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const isFullBleed = location.pathname === '/chat';
  return (
    <div className="grid grid-cols-[220px_minmax(0,1fr)] min-h-screen" data-theme={appName}>
      <Sidebar appName={appName} onAppChange={onAppChange} gatewayStatus={gatewayStatus} />
      <main className="min-w-0 flex flex-col">
        <header className="sticky top-0 z-10 flex items-center gap-6 h-14 px-8 border-b border-border bg-bg/80 backdrop-blur-sm">
          <div className="flex items-center gap-3 mr-auto">
            <h1 className="text-base font-medium text-text m-0">Onboard</h1>
            <span className="text-[11px] font-mono uppercase tracking-wider text-text-muted">{appName}</span>
          </div>
          <SearchBar onSearch={(value) => navigate(`/search?q=${encodeURIComponent(value)}`)} />
          <StatusBadge status={gatewayStatus} />
        </header>
        <section className={`flex-1 w-full ${isFullBleed ? '' : 'max-w-[1320px] mx-auto px-8 py-6'}`}>
          <Outlet />
        </section>
      </main>
    </div>
  );
}
