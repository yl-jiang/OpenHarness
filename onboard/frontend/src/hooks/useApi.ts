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
  const [version, setVersion] = useState(0);
  const hasData = useRef(false);

  useEffect(() => {
    hasData.current = false;
    setData(null);
    setError(null);
    setLoading(enabled);
  }, [enabled, ...deps]);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps, version]);

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
