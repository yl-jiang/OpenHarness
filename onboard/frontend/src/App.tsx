import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { createBrowserRouter, RouterProvider, useRouteError } from 'react-router-dom';

import { api } from './api/client';
import type { AppName } from './api/types';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';

const retried = new Set<string>();

function lazyWithRetry<T extends React.ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
) {
  return lazy(() =>
    factory().catch((err: Error) => {
      const msg = err?.message ?? '';
      if (msg.includes('Failed to fetch dynamically imported module') && !retried.has(msg)) {
        retried.add(msg);
        window.location.reload();
      }
      throw err;
    }),
  );
}

// Lazy-loaded pages for code splitting
const Chat = lazyWithRetry(() => import('./pages/Chat').then((m) => ({ default: m.Chat })));
const Decisions = lazyWithRetry(() => import('./pages/Decisions').then((m) => ({ default: m.Decisions })));
const Entries = lazyWithRetry(() => import('./pages/Entries').then((m) => ({ default: m.Entries })));
const FeedDigests = lazyWithRetry(() => import('./pages/FeedDigests').then((m) => ({ default: m.FeedDigests })));
const Highlights = lazyWithRetry(() => import('./pages/Highlights').then((m) => ({ default: m.Highlights })));
const RecordDetail = lazyWithRetry(() => import('./pages/RecordDetail').then((m) => ({ default: m.RecordDetail })));
const Records = lazyWithRetry(() => import('./pages/Records').then((m) => ({ default: m.Records })));
const Reports = lazyWithRetry(() => import('./pages/Reports').then((m) => ({ default: m.Reports })));
const ReportView = lazyWithRetry(() => import('./pages/ReportView').then((m) => ({ default: m.ReportView })));
const Search = lazyWithRetry(() => import('./pages/Search').then((m) => ({ default: m.Search })));
const Settings = lazyWithRetry(() => import('./pages/Settings').then((m) => ({ default: m.Settings })));
const Todos = lazyWithRetry(() => import('./pages/Todos').then((m) => ({ default: m.Todos })));
const Projects = lazyWithRetry(() => import("./pages/Projects").then((m) => ({ default: m.Projects })));
const ProjectDetail = lazyWithRetry(() => import("./pages/ProjectDetail").then((m) => ({ default: m.ProjectDetail })));
const ProjectInbox = lazyWithRetry(() => import("./pages/ProjectInbox").then((m) => ({ default: m.ProjectInbox })));
const Memory = lazyWithRetry(() => import("./pages/Memory").then((m) => ({ default: m.Memory })));
const Health = lazyWithRetry(() => import("./pages/Health").then((m) => ({ default: m.Health })));

function PageLoader() {
  return (
    <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />
  );
}

function Loader({ appName }: { appName: AppName }) {
  return <Suspense fallback={<PageLoader />}><Dashboard appName={appName} /></Suspense>;
}

function ErrorFallback() {
  const error = useRouteError() as Error;
  return (
    <div className="flex min-h-screen items-center justify-center bg-base">
      <div className="flex flex-col items-center gap-4 text-center">
        <p className="text-lg font-semibold text-primary">Something went wrong</p>
        <p className="text-sm text-secondary max-w-md">{error?.message ?? 'An unexpected error occurred.'}</p>
        <button
          className="rounded-md bg-accent px-4 py-2 text-sm text-white hover:opacity-90"
          onClick={() => window.location.reload()}
        >
          Reload page
        </button>
      </div>
    </div>
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
        if (!cancelled) setGatewayStatus(status.status);
      })
      .catch(() => {
        if (!cancelled) setGatewayStatus('unknown');
      });
    return () => { cancelled = true; };
  }, [appName]);

  const SuspenseLoader = ({ children }: { children: React.ReactNode }) => (
    <Suspense fallback={<PageLoader />}>{children}</Suspense>
  );

  const router = useMemo(() => createBrowserRouter([
    {
      element: <Layout appName={appName} onAppChange={setAppName} gatewayStatus={gatewayStatus} />,
      errorElement: <ErrorFallback />,
      children: [
        { index: true, element: <Loader appName={appName} /> },
        { path: 'entries', element: <SuspenseLoader><Entries appName={appName} /></SuspenseLoader> },
        { path: 'records', element: <SuspenseLoader><Records appName={appName} /></SuspenseLoader> },
        { path: 'records/:id', element: <SuspenseLoader><RecordDetail appName={appName} /></SuspenseLoader> },
        { path: 'health', element: <SuspenseLoader><Health /></SuspenseLoader> },
        { path: 'todos', element: <SuspenseLoader><Todos appName={appName} /></SuspenseLoader> },
        { path: 'reports', element: <SuspenseLoader><Reports appName={appName} /></SuspenseLoader> },
        { path: 'reports/:id', element: <SuspenseLoader><ReportView appName={appName} /></SuspenseLoader> },
        { path: 'feeds', element: <SuspenseLoader><FeedDigests appName={appName} /></SuspenseLoader> },
        { path: 'feeds/:id', element: <SuspenseLoader><FeedDigests appName={appName} /></SuspenseLoader> },
        { path: 'search', element: <SuspenseLoader><Search appName={appName} /></SuspenseLoader> },
        { path: 'chat', element: <SuspenseLoader><Chat appName={appName} /></SuspenseLoader> },
        { path: 'settings', element: <SuspenseLoader><Settings appName={appName} /></SuspenseLoader> },
        { path: 'projects', element: <SuspenseLoader><Projects appName={appName} /></SuspenseLoader> },
        { path: 'projects/inbox', element: <SuspenseLoader><ProjectInbox appName={appName} /></SuspenseLoader> },
        { path: 'projects/:id', element: <SuspenseLoader><ProjectDetail appName={appName} /></SuspenseLoader> },
        { path: 'memory', element: <SuspenseLoader><Memory appName={appName} /></SuspenseLoader> },
        { path: 'decisions', element: <SuspenseLoader><Decisions /></SuspenseLoader> },
        { path: 'highlights', element: <SuspenseLoader><Highlights /></SuspenseLoader> },
      ],
    },
  ]), [appName, gatewayStatus]);

  return <RouterProvider router={router} />;
}
