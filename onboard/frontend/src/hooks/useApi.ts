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
}

export function useApi<T>(loader: () => Promise<T>, deps: unknown[], options: ApiOptions = {}): ApiState<T> {
  const { refreshIntervalMs } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const hasData = useRef(false);

  useEffect(() => {
    hasData.current = false;
    setData(null);
    setError(null);
    setLoading(true);
  }, deps);

  useEffect(() => {
    let cancelled = false;
    setLoading(!hasData.current);
    setError(null);
    loader()
      .then((result) => {
        if (!cancelled) {
          hasData.current = true;
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
    return () => {
      cancelled = true;
    };
  }, [...deps, version]);

  useEffect(() => {
    if (!refreshIntervalMs) {
      return;
    }
    const interval = window.setInterval(() => {
      setVersion((value) => value + 1);
    }, refreshIntervalMs);
    return () => window.clearInterval(interval);
  }, [...deps, refreshIntervalMs]);

  return { data, error, loading, reload: () => setVersion((value) => value + 1) };
}
