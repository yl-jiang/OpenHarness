import { useCallback, useEffect, useState } from 'react';

import type { InsightDomain } from '../api/types';

const _insightGenerating = new Map<InsightDomain, string | null>();
const _insightGenListeners = new Map<InsightDomain, Set<() => void>>();

function getInsightGenerating(domain: InsightDomain): string | null {
  return _insightGenerating.get(domain) ?? null;
}

function setInsightGenerating(domain: InsightDomain, value: string | null): void {
  _insightGenerating.set(domain, value);
  for (const listener of _insightGenListeners.get(domain) ?? []) listener();
}

export function useInsightGenerating(domain: InsightDomain): [string | null, (value: string | null) => void] {
  const [generating, setLocalGenerating] = useState<string | null>(() => getInsightGenerating(domain));

  useEffect(() => {
    setLocalGenerating(getInsightGenerating(domain));
    const listener = () => setLocalGenerating(getInsightGenerating(domain));
    if (!_insightGenListeners.has(domain)) _insightGenListeners.set(domain, new Set());
    _insightGenListeners.get(domain)!.add(listener);
    return () => { _insightGenListeners.get(domain)?.delete(listener); };
  }, [domain]);

  const setter = useCallback((value: string | null) => setInsightGenerating(domain, value), [domain]);
  return [generating, setter];
}
