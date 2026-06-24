import { useEffect, useRef, useState } from 'react';

export const LIVE_REFRESH_INTERVAL_MS = 5000;

interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

interface ApiOptions {
  refreshIntervalMs?: number;
  /** When explicitly `false`, skip the loader entirely (no request, no loading state). */
  enabled?: boolean;
}

export function useApi<T>(loader: () => Promise<T>, deps: unknown[], options: ApiOptions = {}): ApiState<T> {
  const { refreshIntervalMs, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reloadCounter, setReloadCounter] = useState(0);
  const hasData = useRef(false);
  const prevDataRef = useRef<string | null>(null);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  // Reset on deps change
  useEffect(() => {
    hasData.current = false;
    prevDataRef.current = null;
    setData(null);
    setError(null);
    setLoading(enabled);
  }, [enabled, ...deps]);

  // Initial fetch + manual reload
  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(!hasData.current);
    setError(null);
    loaderRef.current()
      .then((result) => {
        if (!cancelled) {
          hasData.current = true;
          const serialized = JSON.stringify(result);
          prevDataRef.current = serialized;
          setData(result);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps, reloadCounter]);

  // Polling — fetch directly in interval, only update state when data changes
  useEffect(() => {
    if (!refreshIntervalMs || !enabled) return;
    const interval = window.setInterval(() => {
      loaderRef.current()
        .then((result) => {
          const serialized = JSON.stringify(result);
          if (serialized !== prevDataRef.current) {
            prevDataRef.current = serialized;
            setData(result);
          }
        })
        .catch(() => {
          // Silently ignore polling errors — the last known data stays visible
        });
    }, refreshIntervalMs);
    return () => window.clearInterval(interval);
  }, [enabled, ...deps, refreshIntervalMs]);

  return { data, error, loading, reload: () => setReloadCounter((v) => v + 1) };
}
