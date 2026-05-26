import { useEffect, useRef, useState } from 'react';

interface StatsCardProps {
  label: string;
  value: number | string;
  hint?: string;
}

function useCountUp(target: number, duration = 800): number {
  const [current, setCurrent] = useState(0);
  const startTime = useRef<number | null>(null);
  const frameRef = useRef<number>(0);

  useEffect(() => {
    if (target === 0) { setCurrent(0); return; }
    startTime.current = null;

    function animate(ts: number) {
      if (!startTime.current) startTime.current = ts;
      const progress = Math.min((ts - startTime.current) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setCurrent(Math.round(eased * target));
      if (progress < 1) frameRef.current = requestAnimationFrame(animate);
    }

    frameRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frameRef.current);
  }, [target, duration]);

  return current;
}

export function StatsCard({ label, value, hint }: StatsCardProps) {
  const isNumber = typeof value === 'number';
  const displayed = useCountUp(isNumber ? value : 0);

  return (
    <article className="p-5 rounded-lg border border-border bg-surface-1 hover:bg-surface-2 transition-colors group">
      <div className="font-mono text-3xl font-semibold text-text tracking-tight animate-[count-up_0.4s_ease-out_both]">
        {isNumber ? displayed.toLocaleString() : value}
      </div>
      <div className="mt-1.5 text-[12px] uppercase tracking-wider text-text-muted font-medium">{label}</div>
      {hint ? <div className="mt-1 text-[11px] text-text-muted">{hint}</div> : null}
    </article>
  );
}
