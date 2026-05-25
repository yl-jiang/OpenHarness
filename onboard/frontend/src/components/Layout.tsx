import { Outlet, useNavigate } from 'react-router-dom';

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
  return (
    <div className={`app-shell theme-${appName}`}>
      <Sidebar appName={appName} onAppChange={onAppChange} gatewayStatus={gatewayStatus} />
      <main className="main">
        <header className="topbar">
          <div>
            <span className="eyebrow">{appName}</span>
            <h1>Onboard</h1>
          </div>
          <SearchBar onSearch={(value) => navigate(`/search?q=${encodeURIComponent(value)}`)} />
          <StatusBadge status={gatewayStatus} />
        </header>
        <section className="content">
          <Outlet />
        </section>
      </main>
    </div>
  );
}
