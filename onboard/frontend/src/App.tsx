import { useEffect, useState } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import { api } from './api/client';
import type { AppName } from './api/types';
import { Layout } from './components/Layout';
import { Chat } from './pages/Chat';
import { Dashboard } from './pages/Dashboard';
import { Decisions } from './pages/Decisions';
import { Entries } from './pages/Entries';
import { Highlights } from './pages/Highlights';
import { RecordDetail } from './pages/RecordDetail';
import { Records } from './pages/Records';
import { Reports } from './pages/Reports';
import { ReportView } from './pages/ReportView';
import { Search } from './pages/Search';
import { Settings } from './pages/Settings';
import { Stats } from './pages/Stats';
import { Todos } from './pages/Todos';

function initialApp(): AppName {
  return localStorage.getItem('onboard-app') === 'wolo' ? 'wolo' : 'solo';
}

export function App() {
  const [appName, setAppName] = useState<AppName>(initialApp);
  const [gatewayStatus, setGatewayStatus] = useState('unknown');

  useEffect(() => {
    localStorage.setItem('onboard-app', appName);
    let cancelled = false;
    api
      .gatewayStatus(appName)
      .then((status) => {
        if (!cancelled) {
          setGatewayStatus(status.status);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setGatewayStatus('unknown');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [appName]);

  return (
    <BrowserRouter>
      <Routes>
        <Route
          element={
            <Layout appName={appName} onAppChange={setAppName} gatewayStatus={gatewayStatus} />
          }
        >
          <Route index element={<Dashboard appName={appName} />} />
          <Route path="entries" element={<Entries appName={appName} />} />
          <Route path="records" element={<Records appName={appName} />} />
          <Route path="records/:id" element={<RecordDetail appName={appName} />} />
          <Route path="todos" element={<Todos appName={appName} />} />
          <Route path="reports" element={<Reports appName={appName} />} />
          <Route path="reports/:id" element={<ReportView appName={appName} />} />
          <Route path="stats" element={<Stats appName={appName} />} />
          <Route path="search" element={<Search appName={appName} />} />
          <Route path="chat" element={<Chat appName={appName} />} />
          <Route path="settings" element={<Settings appName={appName} />} />
          <Route path="decisions" element={<Decisions />} />
          <Route path="highlights" element={<Highlights />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
