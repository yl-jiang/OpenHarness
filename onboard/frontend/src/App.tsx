import { lazy, Suspense, useEffect, useState } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import { api } from './api/client';
import type { AppName } from './api/types';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';

// Lazy-loaded pages for code splitting
const Chat = lazy(() => import('./pages/Chat').then((m) => ({ default: m.Chat })));
const Decisions = lazy(() => import('./pages/Decisions').then((m) => ({ default: m.Decisions })));
const Entries = lazy(() => import('./pages/Entries').then((m) => ({ default: m.Entries })));
const FeedDigests = lazy(() => import('./pages/FeedDigests').then((m) => ({ default: m.FeedDigests })));
const Highlights = lazy(() => import('./pages/Highlights').then((m) => ({ default: m.Highlights })));
const RecordDetail = lazy(() => import('./pages/RecordDetail').then((m) => ({ default: m.RecordDetail })));
const Records = lazy(() => import('./pages/Records').then((m) => ({ default: m.Records })));
const Reports = lazy(() => import('./pages/Reports').then((m) => ({ default: m.Reports })));
const ReportView = lazy(() => import('./pages/ReportView').then((m) => ({ default: m.ReportView })));
const Search = lazy(() => import('./pages/Search').then((m) => ({ default: m.Search })));
const Settings = lazy(() => import('./pages/Settings').then((m) => ({ default: m.Settings })));
const Stats = lazy(() => import('./pages/Stats').then((m) => ({ default: m.Stats })));
const Todos = lazy(() => import('./pages/Todos').then((m) => ({ default: m.Todos })));
const Projects = lazy(() => import("./pages/Projects").then((m) => ({ default: m.Projects })));
const ProjectDetail = lazy(() => import("./pages/ProjectDetail").then((m) => ({ default: m.ProjectDetail })));

function PageLoader() {
  return (
    <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />
  );
}

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
          <Route path="entries" element={<Suspense fallback={<PageLoader />}><Entries appName={appName} /></Suspense>} />
          <Route path="records" element={<Suspense fallback={<PageLoader />}><Records appName={appName} /></Suspense>} />
          <Route path="records/:id" element={<Suspense fallback={<PageLoader />}><RecordDetail appName={appName} /></Suspense>} />
          <Route path="todos" element={<Suspense fallback={<PageLoader />}><Todos appName={appName} /></Suspense>} />
          <Route path="reports" element={<Suspense fallback={<PageLoader />}><Reports appName={appName} /></Suspense>} />
          <Route path="reports/:id" element={<Suspense fallback={<PageLoader />}><ReportView appName={appName} /></Suspense>} />
          <Route path="feeds" element={<Suspense fallback={<PageLoader />}><FeedDigests appName={appName} /></Suspense>} />
          <Route path="feeds/:id" element={<Suspense fallback={<PageLoader />}><FeedDigests appName={appName} /></Suspense>} />
          <Route path="stats" element={<Suspense fallback={<PageLoader />}><Stats appName={appName} /></Suspense>} />
          <Route path="search" element={<Suspense fallback={<PageLoader />}><Search appName={appName} /></Suspense>} />
          <Route path="chat" element={<Suspense fallback={<PageLoader />}><Chat appName={appName} /></Suspense>} />
          <Route path="settings" element={<Suspense fallback={<PageLoader />}><Settings appName={appName} /></Suspense>} />
          <Route path="projects" element={<Suspense fallback={<PageLoader />}><Projects appName={appName} /></Suspense>} />
          <Route path="projects/:id" element={<Suspense fallback={<PageLoader />}><ProjectDetail appName={appName} /></Suspense>} />
          <Route path="decisions" element={<Suspense fallback={<PageLoader />}><Decisions /></Suspense>} />
          <Route path="highlights" element={<Suspense fallback={<PageLoader />}><Highlights /></Suspense>} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
