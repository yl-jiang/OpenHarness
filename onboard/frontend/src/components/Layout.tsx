import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useState } from 'react';

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
  const isDashboard = location.pathname === '/';
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="layout-grid grid grid-cols-[220px_minmax(0,1fr)] min-h-screen" data-theme={appName}>
      {/* Skip to content link for keyboard users */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[200] focus:px-4 focus:py-2 focus:bg-surface-2 focus:border focus:border-border focus:rounded-md focus:text-sm focus:text-text"
      >
        Skip to content
      </a>
      {/* Mobile backdrop */}
      <div
        className="sidebar-backdrop hidden"
        onClick={() => setSidebarOpen(false)}
      />

      <div className={`sidebar-panel ${sidebarOpen ? 'sidebar-open' : ''}`}>
        <Sidebar
          appName={appName}
          onAppChange={(app) => { onAppChange(app); setSidebarOpen(false); }}
          gatewayStatus={gatewayStatus}
        />
      </div>

      <main className="min-w-0 flex flex-col">
        {/* Mobile header with hamburger */}
        <div className="mobile-header hidden items-center gap-3 px-4 py-3 border-b border-border bg-bg/80 backdrop-blur-sm">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-1.5 text-text-muted hover:text-text transition-colors rounded"
            aria-label="Toggle navigation"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 12h18M3 6h18M3 18h18" />
            </svg>
          </button>
          <span className="text-sm font-medium text-text">Onboard</span>
          <span className="text-[11px] font-mono uppercase tracking-wider text-text-muted">{appName}</span>
          <StatusBadge status={gatewayStatus} />
        </div>

        <header className="sticky top-0 z-10 items-center gap-6 h-14 px-4 sm:px-8 border-b border-border bg-bg/80 backdrop-blur-sm hidden md:flex">
          <div className="flex items-center gap-3 mr-auto">
            <h1 className="text-base font-medium text-text m-0">Onboard</h1>
            <span className="text-[11px] font-mono uppercase tracking-wider text-text-muted">{appName}</span>
          </div>
          <SearchBar key={location.pathname} onSearch={(value) => navigate(`/search?q=${encodeURIComponent(value)}`)} globalShortcut />
          <StatusBadge status={gatewayStatus} />
        </header>
        <section id="main-content" className={`flex-1 w-full content-area ${isFullBleed ? '' : isDashboard ? 'px-4 sm:px-8 py-6' : 'max-w-[1320px] mx-auto px-4 sm:px-8 py-6'}`}>
          <Outlet />
        </section>
      </main>
    </div>
  );
}
